# Design

## Context

约束来自现有代码，不是假设（动机见 `proposal.md`，需求见 `specs/`）：

- 引擎契约是同步纯函数：`ParseEngine.parse(file_bytes, filename, config) -> EngineOutput`（`app/parsing/base.py`），无 IO、无重试语义，异常只有 `ParseError`/`UnknownEngineError`。
- 图节点按 `doc_type` 写死路由，节点内部才看策略的 `engine`：`make_engine_node` 先取 `config.engine`，未注册或 `supports(doc_type)` 为假时回落该类型的兜底引擎（`app/pipelines/parse/graph.py`）。因此"引擎不支持该类型"会在运行期被静默吞掉。
- 注册表已具备 `engine_names()` 与 `engines_for(doc_type)`（`app/parsing/registry.py`），缺的只是暴露接口。
- 切块是单一函数 `build_chunks(blocks, hint: int)`，唯一参数 `chunk_hint` 由 `index_service._chunk_hint_for()` 从策略读；解析阶段完全不切块。
- 所有异步任务共用 `parse` 队列，worker 以 `--pool=solo` 启动（`scripts/start-worker.ps1`、`docker-compose.release.yml`），`watchdog.sweep` 也在该队列。
- 解析时限：软 900s / 硬 960s；运行锁 5400s。
- `document_chunk` 没有 `metadata`/`parent_chunk_id` 列，也不记录切块器名。

## Goals / Non-Goals

**Goals:**

- 新增 `mineru` 引擎，形态为"本机独立服务 + HTTP 调用"，不改变引擎契约的同步性。
- 引擎选择在"策略校验、图节点、队列路由"三处使用同一套解析规则，避免投错队列。
- 三种切块策略共享一个配置对象与一个入口函数，存量调用与测试不破。
- 本期零数据库迁移。

**Non-Goals:**

- 不做 MinerU 失败自动降级到 `pdf_layout`。
- 不引入 `BlockType.FORMULA`，不动前端块类型枚举。
- 不把 MinerU 抽取的图片转存对象存储。
- 不改动解析图的结构（不加节点、不加边）。
- 不实现 `parent_child`（方案见后文二期章节）。

## Decisions

### D1 引擎形态：本机独立服务 + httpx，而非进程内 SDK

MinerU 以 `mineru-api` 形式常驻本机（默认 `http://127.0.0.1:8000`），`MineruEngine` 只做三件事：把文件字节与开关组装成 multipart 请求、解析响应、映射为 `EngineOutput`。

- 备选：worker 进程内 `import mineru`。否决理由：torch + 模型权重让 worker 镜像膨胀数 GB；模型必须常驻，加载时间计入 900s 软上限；GPU 显存被解析进程独占后无法再跑任何其它队列；单元格致失败无法归类为"服务不可用"。
- 端点与字段随 MinerU 版本漂移，因此把调用收敛到一个私有方法 `_request(file_bytes, filename, config) -> dict`，上层只依赖归一化后的中间结构。实现时以本地安装版本的实际响应为准，spec 不受影响。

### D2 公式映射为段落块

MinerU 的公式（LaTeX）写成 `block_type=paragraph`，`block_metadata={"kind": "formula", "latex": true}`。

- 备选：新增 `BlockType.FORMULA`。否决理由：枚举扩散到前端渲染、抽取提示词、切块启发式，收益只是语义好看；题干与公式本就常在同一段上下文里，作为段落反而更利于切块和检索。

### D3 图片只登记来源，不落盘

`block_type=image`，`content` 为空，`block_metadata={"resource": "<文件名>"}`。

- 备选：下载到 MinIO。否决理由：需要新的 bucket/key 规范、生命周期清理、以及引用侧改造；图片对当前检索链路没有贡献，先不做。若后续要做多模态检索，再补。

### D4 错误分类：新增可重试码 `mineru_unavailable`

在 `TaskErrorCode` 增加 `mineru_unavailable`（可重试），引擎侧抛 `MineruUnavailableError(ParseError)`，携带 `task_error_code = "mineru_unavailable"`；`classify_exception` 先读异常的该属性，因此无需改分类逻辑。

- 连接失败 / 读超时 / 5xx / 429 → `mineru_unavailable`。
- 200 但结构不合法、正文为空、415/400/加密文件 → `parse_error`（不可重试）。
- 备选：复用 `llm_unavailable`。否决理由：那是模型服务的语义，混用会让运维根据错误码定位问题时误判。

超时用 `httpx.Timeout(mineru_timeout_seconds, connect=10)`，默认 300s，且硬性要求小于解析软上限。

### D5 队列隔离：新增 `parse.mineru`，路由在投递点决定

```
parse_task_service.execute/create/retry  --.
                                          |
task_watchdog 重投 -----------------------+--> resolve_parse_queue(task_id)
                                                    |
                       策略.engine 已注册 且 supports(doc_type)?
                                    |                     |
                                   yes                    no
                                    |                     |
                          "parse.mineru"?          "parse"(默认)
```

- 抽一个 `resolve_effective_engine(doc_type, engine_name)` 到 `app/parsing/routing.py`，图节点、策略校验、队列路由三处共用，消除"图里静默回落、队列却按 mineru 投"的不一致。
- 投递口 `_default_dispatch` 目前只拿 `task_id`，改为用独立会话查任务 → 策略 → 有效引擎，然后 `apply_async(args=[id], queue=...)`。`set_dispatcher` 的测试注入点保持不变。
- 备选：Celery `task_routes` 静态按任务名路由。否决理由：路由粒度是任务名，无法按策略区分。
- 备选：`parse.document` 在 worker 内二次投递。否决理由：任务会被标 RUNNING 两次，与心跳/看护/运行锁语义冲突，还多一次消息往返。

### D6 切块器：一个配置对象 + 一个字典，不拆包

保留 `app/services/chunking.py` 作为唯一入口，内部加：

```python
@dataclass(frozen=True)
class ChunkConfig:
    strategy: str = "block_merge"
    size: int = 800
    overlap: int = 0

def resolve_chunk_config(config: Mapping | None) -> ChunkConfig
def build_chunks(blocks, config: int | ChunkConfig | None = None) -> list[ChunkDraft]
_STRATEGIES = {"block_merge": ..., "fixed_window": ..., "heading_section": ...}
```

- `build_chunks` 保留接受 `int` 的旧签名（等价 `block_merge` + 该长度），因此现有单测与调用点零改动即可运行。
- `index_service._chunk_hint_for` 改为 `_chunk_config_for`，返回 `ChunkConfig`。
- `heading_section` 的二次切分直接复用 `fixed_window` 的实现，不复制粘贴。
- 备选：新建 `app/services/chunkers/` 包 + 注册表装饰器。否决理由：三种策略、无外部扩展需求，包结构带来的间接层大于收益；真需要扩展时再抽注册表。

### D7 校验分两层，规则只写一次

- `app/schemas/parse.py`：pydantic validator 校验 `config.chunk` 的形状、枚举、数值范围、参数与策略的匹配关系（400，字段级报错）。
- `ParseStrategyService.create/update`：校验 `has_engine(engine)` **且** `supports(doc_type)`，以及 `engine=mineru` 时 `mineru_base_url` 非空。
- 两处共用一个 `app/services/chunk_config.py::parse_chunk_config()`，避免规则漂移。

### D8 引擎候选接口的路径顺序

新增 `GET /parse-strategies/engines`，**必须**声明在 `GET /parse-strategies/{strategy_id}` 之前，否则 `engines` 会被当成 id 解析而报 422。

返回 `[{name, display_name, doc_types}]`，直接由 `engine_names()` + `engines_for()` 组装；`display_name` 作为引擎类属性新增，缺省回退 `name`。前端改为启动时拉取，失败时回退到本地常量（覆盖灰度期旧后端）。

### D9 切块配置不新增数据库列

切块器名与参数可经 `document_chunk.parse_task_id -> parse_task.strategy_id -> strategy.config.chunk` 推导，本期不落列、不迁移。

- 备选：给 `document_chunk` 加 `chunker` 列。否决理由：单独为可观测性加一次迁移不划算；二期引入 `parent_chunk_id` 时一起加（见二期章节），那时需要一次迁移同时解决"父子关系 + 切块器快照"。

### D10 策略表单按策略显隐参数

`overlap` 仅 `fixed_window` 可见，`size` 三种都可见（`block_merge`/`heading_section` 下是长度上限），切块策略下拉旁展示该策略的一句话说明。前端不再暴露原始 JSON 编辑入口（后端仍接受 JSON，便于脚本）。

## 二期：parent_child（本次不实现，仅登记）

目标：检索命中细粒度子块，返回时带完整父节上下文，兼顾召回精度与上下文完整性。

```
parent chunk (level=1)  <- heading_section 产出的节，落一行，不向量化
   |
   +-- child chunk (level=2) <- 该节按 fixed_window 再切，落行，向量化
```

数据模型（需一次迁移）：

- `document_chunk` 增加 `parent_chunk_id UUID NULL REFERENCES document_chunk(id) ON DELETE CASCADE`、`chunk_level SMALLINT NOT NULL DEFAULT 1`。
- 同一次索引构建中先写父块、再写子块；父块 `embedding` 为 NULL，`embedding_model` 为空。
- 增加 `(parent_chunk_id)` 索引，用于命中子块后取父内容。

检索侧改动：

- `retrieval_service` 命中子块后按 `parent_chunk_id` 批量取父内容，按父块去重后作为引用返回。
- `AskView` 的引用展示改为"父块内容 + 命中的子块高亮"，需前端配合。
- `index_service.purge_version_chunks` 依赖级联删除，不额外改动。

切块侧改动：

- `heading_section` 增加 `emit_parents` 开关；`fixed_window` 在父子模式下的输出作为子块，`heading_path` 与父块一致。

验收：同一份文档在父子模式下，子块数量约为父块的 2~5 倍；命中子块返回的引用内容包含完整节；重建索引后不残留孤儿行。

## Risks / Trade-offs

- [MinerU 端点/字段随版本漂移] → 调用收敛在 `_request`，配一份固定的响应样例做契约测试；升级 MinerU 只改这一处。
- [MinerU 单次耗时逼近 960s 硬上限] → 默认超时 300s 且强制小于软上限；超时归可重试，不占用降级预算。真需要更长时限时调环境变量，不动代码。
- [`parse.mineru` 无消费者导致任务长期 pending] → 看护只看 RUNNING，pending 不会被判死，堆积是静默的；部署检查项写进 README，并在策略保存时给出"确认已部署 worker"的提示文案。
- [队列路由与图内引擎不一致] → 共用 `resolve_effective_engine`，并用单测锁定"策略 engine 不被支持时路由回默认队列"。
- [固定窗口 + 标题前缀导致实际长度超出 `size`] → 明确定义"前缀计入 size"，测试断言上限。
- [存量策略行为被改变] → 无 `chunk` 子对象时走 `block_merge` + `chunk_hint`，单测锁定片段数量不变。
- [前端灰度期拉到旧后端没有 engines 接口] → 请求失败回退本地常量。

## Migration Plan

无数据库迁移。

1. 本机安装并启动 MinerU 服务，确认 `mineru-api` 端口可用。
2. 配置 `mineru_base_url`（若服务在另一台 GPU 机，填其地址）、`mineru_timeout_seconds`、`mineru_lang`。
3. 启动 `parse.mineru` 队列的 worker（`scripts/start-worker.ps1 -Queues parse.mineru`；release 编排里新增一个 `--pool=solo` 的服务）。
4. 发布后端与前端；存量策略无需改动，切块走缺省 `block_merge`。
5. 需要 MinerU 的文档：新建/修改策略把 engine 设为 `mineru`，重解析即可；不需要的文档不受影响。

回滚：

- 把策略 engine 改回 `pdf_layout`，或停用该策略；已产出的块与片段不回滚（重新解析才会变）。
- 停掉 `parse.mineru` worker 时在途任务留在 pending，恢复 worker 后继续；若决定弃用，需先把相关策略切回默认引擎，避免新任务继续投递。

## Open Questions

- 本地 MinerU 版本的 `file_parse` 具体入参与返回结构（zip 还是 JSON、字段命名）——实现时以实际安装版本为准，只影响 `_request` 一处的适配代码，不改变本设计与 spec。
- 是否需要把 `mineru_backend`（pipeline/vlm）暴露到策略表单——本期只在环境配置里提供，若运维频繁切换再上界面。
