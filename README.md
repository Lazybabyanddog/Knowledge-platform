# 企业知识库平台

OpenSpec 驱动的企业知识库后端：文档管理、解析流水线、本体与知识图谱。
技术栈 FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL / Redis / Neo4j / S3 兼容存储。

需求与任务见 `openspec/changes/knowledge-base-platform/`。

## 目录

```
backend/
  app/
    api/v1/           HTTP 路由（鉴权、参数校验）
    services/         业务编排与事务边界
    domain/           枚举、策略、表驱动状态机
    infra/            DB 模型/仓储、缓存、对象存储
    parsing/          解析引擎适配层（契约 + 注册表 + 内置引擎）
    pipelines/parse/  LangGraph 解析图（节点、路由、装配）
    pipelines/extract/ LangGraph 抽取图（分块、抽取、校验修复、去重落库）
    workers/          Celery 应用与任务
    schemas/          Pydantic 请求/响应模型
  alembic/            迁移脚本
  tests/              unit + integration
  Dockerfile          单镜像四角色（api / worker / beat / migrate）
deploy/
  gateway/            nginx 网关与 active.conf（当前颜色 + 前端目录）
  releases/<version>/ 前端构建产物，按版本只增不改
  state/              current.json / releases.jsonl / drain.log
scripts/              release.ps1、rollback.ps1、build-frontend.ps1、check_migrations.py
```

## 本地运行

```bash
cp .env.example .env                 # 根目录：数据库/Neo4j/MinIO/Redis/JWT/LLM/种子账号
docker compose up -d                 # PostgreSQL / Redis / Neo4j / MinIO
conda activate ai_platform
cd backend
alembic upgrade head                 # 建表
python -m app.scripts.seed           # 种子数据（管理员、权限目录与内置角色授权、默认解析策略；可重复执行）
uvicorn app.main:app --reload        # API: http://127.0.0.1:8000/docs
celery -A app.workers.celery_app:celery_app worker -Q parse -l info   # 解析 + 抽取 worker
```

另开终端启动前端：

```bash
cd frontend
npm install
npm run dev                          # Web: http://localhost:5173（/api 代理到 8000）
```

种子管理员：`admin / Admin@12345`，可用 `.env` 的 `SEED_ADMIN_USERNAME` / `SEED_ADMIN_PASSWORD` 覆盖。知识抽取需在 `.env` 填写 `LLM_API_KEY`（可选 `LLM_BASE_URL`）；未配置时解析可正常执行，抽取任务会失败。

## 部署与发布（蓝绿 / 单色）

容器发布与上面的 conda 本地开发是两条互斥路径：共用同一套中间件时，宿主的 uvicorn / celery 会和容器 worker 抢同一个队列，只能跑一边。

```powershell
copy .env.example .env                              # 填 SEED_ADMIN_PASSWORD 等
docker compose up -d                                # PostgreSQL / Redis / Neo4j / MinIO
docker compose -f docker-compose.yml -f docker-compose.release.yml up -d gateway
pwsh -File scripts\release.ps1 -Version v20261125-1            # 默认蓝绿：起新色 → 验证 → 切流 → 排空
pwsh -File scripts\release.ps1 -Version v20261126-1 -Mode inplace  # 只保一色：停旧起新，失败按旧镜像回退
pwsh -File scripts\rollback.ps1 -Reason "结果异常"    # 回流量到上一版（单色模式按镜像 tag 重建）
```

- **发布模式是开关**：`-Mode bluegreen|inplace`，缺省读 `.env` 的 `RELEASE_MODE`（未配置即蓝绿）。
- **入口只有网关**：`http://<host>:8080`，`/` 是前端、`/api` 反代到当前颜色；两色 API 只绑 `127.0.0.1:8000/8001`。
- **单镜像四角色**：`kp-backend:<version>` 一份镜像，靠启动命令区分 api / worker / beat / migrate。
- **切换不重启**：`deploy/gateway/conf.d/active.conf` 一次替换同时改上游与前端目录，`nginx -s reload` 生效。
- **迁移只允许 expand**：`scripts/check_migrations.py` 拦截 drop / rename / 收紧可空；含 contract 的版本不能回滚。
- **蓝绿失败不伤线上**：新色 `/readyz` 或冒烟不过就清理新色、保留当前流量；已切流则提示执行 `rollback.ps1`。
- **单色有停机窗口**：原地重建期间服务不可用，失败时自动用上一版本镜像重建回去（首版无旧镜像可退）。
- **排空而非硬停**：蓝绿切流后旧 worker 在后台按 `WORKER_DRAIN_GRACE`（默认 900s）等当前任务跑完。
- **状态可查**：`deploy/state/current.json`（当前版本/颜色/模式/上一版）与 `releases.jsonl`（含失败阶段）。

细节（端口、目录布局、迁移纪律、beat 单实例、版本目录保留策略、失败处理）见 [`deploy/README.md`](deploy/README.md)。

## MinerU 版面解析（可选）

MinerU 用于扫描件、双栏、公式与复杂表格的 PDF 版面解析。平台以**本机独立服务**形态接入：worker 只发 HTTP 请求，不加载模型，因此模型权重与 GPU 依赖不会进后端镜像。

```bash
# 1) 本机安装并启动 MinerU 服务（默认监听 8000），确认可用
curl -s http://127.0.0.1:8000/docs > /dev/null && echo ok

# 2) 在 .env 里指向该服务
MINERU_BASE_URL=http://127.0.0.1:8000
MINERU_TIMEOUT_SECONDS=300      # 必须明显小于 PARSE_SOFT_TIME_LIMIT_SECONDS（默认 900）

# 3) 另开一个窗口/容器，单独消费 MinerU 队列
celery -A app.workers.celery_app:celery_app worker -Q parse.mineru -l info --pool=solo
#   或：pwsh -File scripts\start-worker.ps1 -Queue parse.mineru
```

然后在解析策略里把 `engine` 设为 `mineru`（`doc_type` 只能是 `pdf`）。

- 保存策略时即校验：引擎不支持该 `doc_type`、或 `MINERU_BASE_URL` 为空，直接返回 400，不会把注定失败的任务留给 worker。
- MinerU 任务投递到 `parse.mineru`，与解析、抽取、索引、看护所用的 `parse` 队列隔离；一次分钟级解析不会堵住索引和心跳看护。
- 服务不可达、超时、5xx 归为可重试错误 `mineru_unavailable`，按任务重试预算自动重跑；文档结构不合法归为 `parse_error`，不重试。不会自动降级到 `pdf_layout`——失败原因直接暴露在任务详情里。
- 没有消费者时任务留在队列中等待，不会被丢弃；若不打算使用 MinerU，可以不配 `MINERU_BASE_URL`，其它引擎完全不受影响。
- 容器部署时 `127.0.0.1` 指容器自身，需改成宿主地址（如 `http://host.docker.internal:8000`）或同一网络里的服务名。

## 质量门禁

```bash
cd backend
python -m ruff check app tests
python -m pytest tests -q
python -m alembic check              # 确认模型与迁移无漂移

cd ../frontend
npm run type-check
npm run build
```

## 本体与知识抽取

本体（实体类 / 关系类型）是抽取的约束来源，改一次版本号加一，并单向投影到 Neo4j 的 `:Class` 节点：

```bash
POST   /api/v1/ontology/classes                     {"code": "org", "name": "组织", "attributes": {...}, "status": 0}
PATCH  /api/v1/ontology/classes/<id>                {"parent_id": "<上级类 id>"}    # 成环返回 400
POST   /api/v1/ontology/relations                   {"code": "HAS_PLAINTIFF", "name": "原告", "source_class_id": "...", "target_class_id": "...", "status": 0}
POST   /api/v1/ontology/sync                        # 手工重放投影，返回 {"projected": n}
```

- 编码以字母开头，仅含字母、数字与下划线，总长 2–64，**大小写均可且大小写敏感**（`HAS_PLAINTIFF` 与 `has_plaintiff` 是两个编码）。
- `status` 创建时必传，`0` = 有效、`1` = 失效；`PATCH` 时可选但只接受 `0`/`1`。失效定义不出现在默认列表中，也不参与抽取约束。
- 兼容性：旧客户端传 `"active"`/`"disabled"` 或不传 `status` 会返回 400；升级迁移会把存量定义的 `status` 统一置 `0`。

抽取链路（`app/pipelines/extract/`）：`chunk_blocks → classify_blocks → extract_entities → extract_relations → schema_validate`，
校验不通过时走 `repair_output` 回环（默认最多 2 次），仍不合法则整批跳过；最后 `dedupe_candidates → persist_candidates`
以 `status=proposed` 写入图库。没有配置 LLM 密钥时自动退化为 `RuleBasedExtractor`，离线也能跑通全链路。

```bash
POST   /api/v1/extraction-tasks                     {"parse_task_id": "<成功状态的解析任务>", "limit_blocks": 500}
GET    /api/v1/extraction-tasks?document_id=<id>&status=succeeded
GET    /api/v1/extraction-tasks/<task_id>
POST   /api/v1/extraction-tasks/<task_id>/retry      # 仅 failed 可重试
POST   /api/v1/extraction-tasks/<task_id>/cancel     # pending/running 可取消

GET    /api/v1/entities?status=proposed&class_code=org&keyword=星海
POST   /api/v1/entities/<id>/confirm                {"name": "新名字"}   # 改名会把旧名沉进 aliases
POST   /api/v1/entities/<id>/reject                 {"reason": "不是实体"}
POST   /api/v1/entities/merge                       {"target_id": "...", "source_ids": ["..."]}
POST   /api/v1/entities/merge/undo                  {"target_id": "...", "merge_id": "..."}   # 默认 7 天窗口
GET    /api/v1/entities/<id>/sources                # 返回证据块与页码
GET    /api/v1/graph/entities/<id>/subgraph?depth=2&include_proposed=false
GET    /api/v1/graph/documents/<document_id>/entities

GET    /api/v1/relations?status=proposed&code=works_at
POST   /api/v1/relations/<id>/confirm
POST   /api/v1/relations/<id>/reject                {"reason": "方向错误"}
```

合并采用"目标实体吸收来源实体"：来源实体状态置 `merged` 并指向目标，别名与证据取并集，
被合并端点上的关系改挂到目标后关系数守恒；撤销依赖 `MergeRecord` 快照，超过窗口或目标已被修改返回 409。

## 文档审批

审批流按文档类型配置（`pdf` / `office` / `text` / `image` / `html`，其他类型返回 400），
步骤支持"角色"或"指定用户"审批人，每种类型至多一个默认流；类型没有默认流时提审直接报错，
不会退化成"无审批直接发布"：

```bash
POST   /api/v1/review-flows                          # 管理员
{"name": "两级审核", "doc_type": "pdf", "is_default": true,
 "steps": [{"name": "内容审核", "approver_type": "role", "approver_value": "reviewer"},
           {"name": "管理员批准", "approver_type": "role", "approver_value": "admin"}]}
GET    /api/v1/review-flows?doc_type=pdf
PATCH  /api/v1/review-flows/<id>                     # 改步骤 / 切默认 / 停用

POST   /api/v1/reviews/submit                        # 贡献者提交，默认取最新版本
{"document_id": "<id>", "document_version_id": "<id>", "flow_id": "<id>"}
GET    /api/v1/reviews?scope=mine                    # 待我审批（当前步骤指派给我）
GET    /api/v1/reviews?scope=all&status=pending      # 管理员全量筛选
GET    /api/v1/reviews/<instance_id>                 # 步骤与流转轨迹
POST   /api/v1/reviews/<instance_id>/approve         {"comment": "同意"}
POST   /api/v1/reviews/<instance_id>/reject          {"comment": "缺少风险披露"}   # 理由必填
POST   /api/v1/reviews/<instance_id>/withdraw        # 仅提交人，仅未结束时
```

提交时把流程快照进实例并冻结版本：重复提交返回 409，审批中上传新版本/删除文档/改状态均返回 409。
最后一步批准后由服务层把 `is_current` 切到被审版本并发布文档；驳回后文档转 `rejected` 并解冻，
撤回后回到 `draft`。同一人重复批准同一实例返回 409，非当前步骤审批人返回 403；
待办列表按提交时间正序返回，当前步骤停留超过 3 个工作日标记 `overdue` 并给出 `waiting_workdays`。

## 组织、角色与文档归属

部门是邻接表树（`department.parent_id`，最多 5 层），人员（`app_user.department_id`）只属于一个部门；
角色与权限全部落库（`role` / `permission` / `role_permission`），内置 4 个角色受 `is_system` 保护，
可新建自定义角色并勾选权限。**权限不再写在前端**：登录后 `GET /api/v1/auth/me` 下发 `permissions`，
后端每次请求都现查库，改权限即时生效、无需重新登录。

```bash
GET    /api/v1/departments                     # user:manage 或 department:manage
POST   /api/v1/departments                     {"name": "技术中心", "parent_id": null, "sort_order": 0}
PATCH  /api/v1/departments/<id>                # 改名 / 移动 / 调序；成环或超过 5 层返回 400
DELETE /api/v1/departments/<id>                # 有子部门或人员时返回 409

GET    /api/v1/roles                           # role:manage
GET    /api/v1/roles/permissions               # 按分组返回权限目录
POST   /api/v1/roles                           {"code": "auditor", "name": "审核员", "permissions": ["document:read", "review:approve"]}
PATCH  /api/v1/roles/<id>                      # 编码不可改；内置角色不可删
DELETE /api/v1/roles/<id>                      # 内置 400，仍被人员或审批流引用时 409

GET    /api/v1/users?department_id=<id>        # 按部门筛选，自动含下级部门
GET    /api/v1/documents?department_id=<id>    # 按上传人所属部门筛选，同样含下级
```

文档列表与详情返回 `created_by_name` / `created_by_department_id` / `created_by_department_name`，
版本历史返回每版 `created_by_name`。归属取**上传人当前档案**（实时 join，不做快照），因此人员换部门后归属跟着变，
停用后仍显示姓名；负责人（`owner_id`）与上传人是两个字段，转移负责人不影响归属。

前端入口：系统 → 部门管理 / 角色管理 / 用户管理；上传人筛选项在文档库工具栏。

## HTML 文档解析

`.html` 是独立文档类型，由 `html_dom` 引擎按 DOM 结构取块，不再按行切源码。
`seed` 会写入该类型的默认策略（`strategy_key=html-default`），正文范围默认整篇 `body`。

| 配置项 | 默认 | 说明 |
| --- | --- | --- |
| `content_selector` | `body` | 正文容器 CSS 选择器（如公报页 `#gb_content`）；未命中时回退整篇 `body`，并在任务告警中提示 |
| `title_selector` | 空 | 额外标题选择器，用于没有 `h1`–`h6`、只靠类名标标题的页面 |
| `merge_titles` | `true` | 相邻标题合并，用于把拆成多行的长标题还原成一条 |
| `drop_selectors` | `[]` | 追加剔除的节点，如面包屑、分享栏 |
| `keep_images` | `false` | 是否把图片转为图片块 |

标题识别顺序为 `title_selector` → `h1`–`h6` → 无块级子节点且类名含 `title`；
`script`/`style`/`noscript`/`template` 等默认不进入产物。HTML 没有分页，所有块 `page_no=1`；
正文为空时任务仍成功，但记为低质量并保留告警。

```bash
POST /api/v1/parse-strategies
{"name": "公报页解析", "doc_type": "html", "engine": "html_dom", "is_default": true,
 "config": {"content_selector": "#gb_content", "merge_titles": true}}

# 存量 HTML 文档重解析：对已有版本重新触发即可，无需重新上传
POST /api/v1/parse-tasks  {"document_version_id": "<id>"}
```

## 检索与问答（RAG）

发布即建索引：审批通过（或把文档恢复到发布态）后版本索引置 `pending` 并投递 `index.document_version`；
worker 取该版本最近的 `succeeded` 解析产物切分、向量化后写入 `document_chunk`，状态转 `ready`。
重建解析任务、退出发布态、软删除都会先清空旧片段，避免脏召回。

前置条件：

- **数据库需启用 pgvector**：`docker compose` 用的是 `pgvector/pgvector:pg16`（与官方 `postgres` 同主版本，
  数据目录兼容），切换镜像后执行 `alembic upgrade head` 建 `vector` 扩展与 `document_chunk`。
- **Embedding 配置**：`.env` 填 `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL`；
  `EMBEDDING_DIM` 必须与 `document_chunk.embedding` 列维度一致（默认 1024），改维度要迁移并全量重建。
- **HTML 先配审批流**：`html` 类型没有默认审批流时提审返回 400，先在「文档审批」里建一条 `doc_type=html` 的默认流。

| `index_status` | 含义 |
| --- | --- |
| `pending` | 已排队，等待 worker |
| `building` | 正在切分 / 向量化 |
| `ready` | 可检索，`chunk_count` 为片段数 |
| `failed` | 失败，`index_error` 给出原因，可重建 |
| `skipped` | 未配置 embedding，跳过（不阻塞发布） |

```bash
# 补配 embedding 后重建（--all 枚举已发布文档的当前版本）
python -m app.scripts.reindex --all
python -m app.scripts.reindex --document-id <id>

# 索引任务与解析共用 parse 队列，worker 启动命令不变
celery -A app.workers.celery_app:celery_app worker -Q parse -l info
```

`reindex --all` 会把所有待重建版本一次性投进 `parse` 队列：批量重建时建议**另起一个只消费 `parse` 的
worker**（或临时把 worker 并发调大），避免与日常解析抢同一个进程。索引按版本先删后建、幂等，
重复执行只会覆盖同版本片段，不会产生重复行。

检索口径：同一空间、文档 `published` 且未软删、版本为 `current_version_id`、密级可见。
管理员可见四级密级，其他角色上限为 `RETRIEVAL_MAX_SENSITIVITY`（默认 `public` + `internal`）。
过滤与排序在同一条 SQL 内完成，按 HNSW 余弦距离升序，先过采样 `RETRIEVAL_OVERSAMPLE` 倍再截断 `top_k`。

```bash
POST /api/v1/retrieval/search
{"query": "年假怎么算", "top_k": 5, "doc_type": "pdf"}
# -> {"items": [{"index": 1, "title": "...", "page_no": 12, "snippet": "...", "score": 0.83}], "total": 1}

POST /api/v1/retrieval/ask
{"question": "年假怎么算"}                       # SSE：sources -> delta* -> done
curl -N -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"question":"年假怎么算"}' http://127.0.0.1:8000/api/v1/retrieval/ask
```

事件语义：`sources`（引用列表，`index` 与答案里的 `[n]` 对应）、`delta`（增量文本）、
`done`（`elapsed_ms`、`sources`）、`error`（模型中途失败，带 `code` / `message`）。
零命中不调用模型，直接回一段固定说明；前端「知识问答」页按同一事件流渲染并支持停止生成。

`503`（`code=50301`）只覆盖两种配置缺失：没有 embedding（无法检索）、没有 LLM（无法作答）。
先看 `.env` 的 `EMBEDDING_API_KEY` / `LLM_API_KEY`，再确认目标版本 `index_status` 为 `ready`。

### 当前边界与后续改造

现在只做「向量召回 + 单轮拼接」，没有关键词召回、重排与查询改写：

- **混合检索**：在 `RetrievalService.search` 旁加一路 `tsvector` / `pg_trgm` 召回，用 RRF 融合排名；
  空间、发布态、密级过滤原样复用，只替换候选集生成。
- **重排**：过采样候选送 rerank 模型再截断 `top_k`，接口契约不变，在校验后加一层即可。
- **分块策略**：`services/chunking.py` 现为段落 → 句末 → 硬切三级回退，标题栈给片段加前缀、表格独立成块；
  换语义分块只需替换 `build_chunks`，落库字段不变。
- **增量索引**：当前按版本整体重建（先删后建，幂等）；文档量大后可按 `parse_block` 哈希只更新变化片段。

## 新增一个解析引擎

解析引擎是"字节 → 内容块"的纯函数：不碰数据库、不碰队列，失败抛 `ParseError`，
这样既能单测，也能被解析图自由替换。

### 1. 遵守适配层契约（`app/parsing/base.py`）

| 类型 | 约定 |
| --- | --- |
| `ParseInput` | 输入：`data`（原始字节）、`file_name`、`mime_type`、`doc_type`（`pdf`/`office`/`image`/`text`/`html`）、`config`（策略配置） |
| `EngineResult` | 输出：`blocks`、`warnings`、`stats`、`needs_ocr`（版面引擎发现无文字层时置 `True`，触发 OCR 分支） |
| `ParsedBlock` | 单个块：`block_type`、`content`、`page_no`、`bbox`、`level`、`parent_index`、`is_discarded`、`metadata`；`seq` 由 `normalize` 节点统一重排，引擎不必填 |
| `ParseEngine` | 基类：类属性 `name`（策略 `engine` 字段取值）、`display_name`、`doc_types`，实现 `parse()` |

### 2. 实现并注册

新建 `app/parsing/engines/dummy.py`：

```python
from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParsedBlock, ParseEngine, ParseInput
from app.parsing.registry import register_engine


@register_engine
class DummyEngine(ParseEngine):
    name = "dummy"
    display_name = "示例引擎"
    doc_types = ("text",)

    def parse(self, source: ParseInput) -> EngineResult:
        text = source.data.decode("utf-8", errors="replace")
        blocks = [
            ParsedBlock(BlockType.PARAGRAPH, line.strip(), seq=index)
            for index, line in enumerate(text.splitlines())
            if line.strip()
        ]
        return EngineResult(
            blocks=blocks,
            stats={"char_count": len(text)},
            warnings=[] if blocks else ["文件没有可解析的文本"],
        )
```

在 `app/parsing/engines/__init__.py` 追加导入，导入即注册：

```python
from app.parsing.engines import dummy, html_dom, ocr, office, pdf_layout, plain_text  # noqa: F401
__all__ = ["dummy", "html_dom", "ocr", "office", "pdf_layout", "plain_text"]
```

注册表同名覆盖，测试里可以直接用假引擎替换真实实现。

### 3. 验证

```bash
# 1) 引擎已注册（单位测试或 REPL）
python -c "from app.parsing import engine_names; print(engine_names())"   # 含 dummy

# 2) 新建策略引用它（admin 登录后）
POST /api/v1/parse-strategies
{"name": "示例策略", "doc_type": "text", "engine": "dummy", "config": {}, "is_default": false}

# 3) 触发解析并查看产物
POST  /api/v1/parse-tasks          {"document_version_id": "<id>", "strategy_id": "<上面返回的 id>"}
GET   /api/v1/parse-tasks/<task_id>
GET   /api/v1/parse-tasks/<task_id>/blocks
```

策略引用了未注册的 `engine` 时，创建/修改策略直接返回 400，不会等到 worker 里才失败。
