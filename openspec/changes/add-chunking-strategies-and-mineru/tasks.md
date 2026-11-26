# Tasks

## 1. 引擎路由共用地基

- [x] 1.1 新增 `app/parsing/routing.py` 的 `resolve_effective_engine(doc_type, engine_name)`（未注册或 `supports(doc_type)` 为假时返回该类型兜底引擎），并让 `app/pipelines/parse/graph.py` 的 `make_engine_node` 改用它；验证：新增单测覆盖"指定且支持/指定但不支持/未指定"三分支，且现有解析图测试全部通过
- [x] 1.2 在 `TaskErrorCode` 增加 `mineru_unavailable` 并标记可重试，新增 `MineruUnavailableError(ParseError)`（携带 `task_error_code`）；验证：单测断言 `classify_exception` 得到该码、`auto_retry_allowed` 对该码返回真，且 `parse_error` 行为不变

## 2. MinerU 引擎

- [x] 2.1 新增 `app/parsing/engines/mineru.py`：引擎声明 `name="mineru"`、`doc_types=("pdf",)`、`display_name`，用 `httpx` 组装 multipart 请求并把连接失败、超时、5xx 映射为 `MineruUnavailableError`，结构不合法映射为 `parse_error`；验证：mock 传输层断言三类错误码映射正确、超时取自配置项
- [x] 2.2 实现产物映射：标题（含层级）、段落、列表、表格（markdown）、公式（段落 + `block_metadata.kind=formula`）、图片（空内容 + `resource`）、页码与坐标、以及"部分页失败"告警；验证：以固定样例响应做映射单测，断言块类型序列、页码、公式与图片元数据、告警内容
- [x] 2.3 在 `app/parsing/engines/__init__.py` 注册引擎，并在 `app/core/config.py` 与 `.env.example` 增加 `mineru_base_url`（默认空）、`mineru_timeout_seconds`（默认 300）、`mineru_lang`、`mineru_backend`；验证：单测断言 `engine_names()` 含 `mineru`、`engines_for("pdf")` 含该引擎、缺省配置值正确
- [x] 2.4 新增 `GET /parse-strategies/engines`，返回 `name/display_name/doc_types`，且路由声明在 `GET /parse-strategies/{strategy_id}` 之前（v1 里为 router 前缀），引擎类补 `display_name` 属性（缺省回退 `name`）；验证：接口测试断言返回项与实际注册表一致，且 `GET /parse-strategies/engines` 不被 id 路由判为 422

## 3. 策略校验与队列隔离

- [x] 3.1 `ParseStrategyService.create/update` 增加校验：引擎必须存在且 `supports(doc_type)`，`engine=mineru` 时 `mineru_base_url` 非空；验证：单测断言三种非法输入均返回 400 且不落库
- [x] 3.2 让 `parse_task_service._default_dispatch` 与延迟投递按 `resolve_effective_engine` 选择队列，`engine=mineru` 投 `parse.mineru`，其余投 `parse`，看护重投复用同一入口；验证：单测用可替换 dispatcher 断言队列名，并覆盖"策略指定 mineru 但 doc_type 不被支持时回落 `parse`"
- [x] 3.3 部署支撑：`scripts/start-worker.ps1` 增加队列参数（默认 `parse`），`docker-compose.release.yml` 增加消费 `parse.mineru` 的 `--pool=solo` worker，README 增补"MinerU 安装、`mineru_base_url` 配置、worker 启动、未部署时的表现"；验证：按 README 命令能在无 MinerU 环境下正常启动其它 worker，且按文档步骤可让 `parse.mineru` 有消费者

## 4. 切块策略

- [x] 4.1 新增 `app/services/chunk_config.py`：`ChunkConfig` 与 `parse_chunk_config(config)`，实现缺省回退（`chunk.size` → `chunk_hint` → 800）与合法性校验（枚举、100~4000、overlap ≤ size/2、参数与策略匹配）；验证：单测覆盖缺省、未知策略名、越界数值、`block_merge + overlap` 四类输入
- [x] 4.2 改造 `app/services/chunking.py`：抽出 `build_chunks(blocks, config: int | ChunkConfig | None)` 与 `_STRATEGIES` 字典，`block_merge` 行为逐字保持；验证：现有 `tests/unit/test_chunking.py` 全部通过，且同一输入产出的片段数量与内容前缀不变
- [x] 4.3 实现 `fixed_window`：按窗口累积、`overlap` 生效、优先在段落/句末收尾、标题前缀计入 `size`、表格仍独立成片段；验证：单测断言相邻片段重叠字数、每段长度上限、不在句中截断、表格片段独立
- [x] 4.4 实现 `heading_section`：以标题块为边界成节、同节正文合并、超长节复用 `fixed_window` 二次切分且保持标题前缀；验证：单测断言三节产出三片段、2000 字节被切多段且每段以该节标题路径开头
- [x] 4.5 `app/schemas/parse.py` 的请求模型接入 `parse_chunk_config`，非法配置返回 400 并指明字段；验证：接口测试断言非法 `chunk` 的创建/更新被拒，合法配置原样保存
- [x] 4.6 `index_service` 的 `_chunk_hint_for` 改为 `_chunk_config_for` 并传给 `build_chunks`，其余索引逻辑不变；验证：单测断言同一解析任务连续两次索引片段数一致（幂等），且策略改为 `heading_section` 并重建后片段数变化

## 5. 前端

- [x] 5.1 `ParseStrategiesView.vue` 增加切块策略下拉与 `size`/`overlap` 输入（`overlap` 仅 `fixed_window` 显示，附策略一句话说明），列表展示各策略的切块配置；验证：表单保存后重新打开回显一致，切换策略时参数显隐正确
- [x] 5.2 引擎候选改为调用 `GET /parse-strategies/engines`，请求失败时回退本地常量，选中引擎后按 `doc_types` 过滤文档类型；验证：断开该接口时表单仍可用，且候选与后端注册表一致

## 6. 集成验证

- [ ] 6.1 端到端：本机启动 MinerU，建 `engine=mineru` 的 PDF 策略，上传含标题/表格/公式的 PDF，完成解析→发布→索引→检索，核对待检查项（块类型分布含表格与公式标记、页码正确、检索能命中）并记录结果
  - 现状：本机未部署 MinerU（`MINERU_BASE_URL` 为空、未安装 `mineru` 包、无服务端口），需先按 README「MinerU 版面解析（可选）」起服务再执行
- [ ] 6.2 队列隔离演练：在一个 MinerU 长任务运行期间提交索引构建并等待看护周期，确认索引完成、`watchdog.sweep` 照常执行（查看任务时间线或 worker 日志）
  - 已实测：临时启动 `-Q parse.mineru --pool=solo` worker，`inspect active_queues` 仅订阅 `parse.mineru`；向其投递 `parse.document` 后 `inspect stats` 计数 +1 且 `parse` 队列积压为 0，说明 MinerU 任务不落 `parse`。长任务并行下的索引与看护周期仍待 MinerU 就绪后演练
- [x] 6.3 兼容性回归：用只有 `chunk_hint` 的存量策略完成一次解析与索引，与变更前的片段数量、块数量对比一致
  - 做法：以 `git show HEAD:backend/app/services/chunking.py` 的旧实现为基线，对「纯段落 / 标题+正文 / 标题+表格 / 长段落硬切 / 空块与丢弃」五组输入比较 `(seq, content, heading_path)`，新旧完全一致；`chunk_hint` 回退由单测与集成测试覆盖

## 二期（parent_child，本次不实现，不属于 apply 范围）

以下条目仅作登记，需另开变更实施；方案细节见 `design.md` 的"二期"章节。

- 迁移：`document_chunk` 增加 `parent_chunk_id`（自引用，级联删除）、`chunk_level`，并补 `parent_chunk_id` 索引；同一次迁移补切块器快照列（`chunker`），解决本期用关联推导可观测性的遗留
- 切块：`heading_section` 增加 `emit_parents`，父块为节、子块为节内 `fixed_window` 结果，二者 `heading_path` 一致
- 索引：同一构建内先父后子，父块不写 embedding，`purge_version_chunks` 依赖级联删除
- 检索：命中子块后按 `parent_chunk_id` 批量取父内容并按父去重，引用返回父块内容 + 命中子块
- 前端：引用展示调整为父内容 + 子块高亮
- 验收：子块数约为父块的 2~5 倍；命中返回完整父节；重建索引后无孤儿行

## Workflow follow-up

- 归档前确认 `openspec validate add-chunking-strategies-and-mineru --strict` 通过。
- 变更归档后，`chunking-strategy` 与 `mineru-parsing` 成为主规格；二期变更应引用它们的既有需求，而不是重写。
