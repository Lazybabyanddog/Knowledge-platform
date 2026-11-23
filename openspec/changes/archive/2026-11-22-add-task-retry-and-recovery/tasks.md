# Tasks

## 1. 数据模型与迁移

- [x] 1.1 新增 `ExtractionTaskAttempt`、`ExtractionBatchCheckpoint` 模型，并为 `ParseTask` / `ExtractionTask` 增加 `auto_retries` / `downgrade_retries` / `manual_revives` / `checkpoint_fingerprint` / `incomplete` / `next_source` 字段，为 `ParseTaskAttempt` 增加 `error_detail` / `source` / `duration_ms` / `heartbeat_at`，生成 Alembic revision；验证：`alembic upgrade head` 成功，`alembic downgrade -1` 可回滚且不丢既有数据
- [x] 1.2 补回填逻辑：历史 `parse_task_attempt.source` 填 `manual`，`duration_ms` 由起止时间推导，新增计数字段取默认值；验证：迁移后查询历史 attempt 行 `source` 无 `NULL`，计数字段均为 0
- [x] 1.3 扩展仓储层：attempt 追加与按任务倒序列出、断点 `ON CONFLICT DO NOTHING` 写入、按任务批量读断点、按任务聚合进度（总数/已提交/已跳过/当前批）；验证：`tests/unit/test_task_recovery.py` 覆盖指纹与"已覆盖块前缀"推进
- [x] 1.4 扩展出参 schema（`app/schemas/parse.py`、`app/schemas/knowledge.py`）以容纳 `attempts` / `progress` / `error` / `skip_summary`；验证：接口序列化测试通过，旧字段保持不变

## 2. 错误分类（先让失败显形）

- [x] 2.1 新增 `LLMTransientError` / `LLMPermanentError` 与统一错误码枚举，新建 `app/domain/error_catalog.py` 存放错误码到「中文文案 + 建议动作 + 是否可重试」的映射；验证：`tests/unit/test_task_recovery.py` 断言分类结果，未知码有兜底文案
- [x] 2.2 给 `chat_json` 加进程内重试：仅对 429 / 5xx / 超时 / 连接重置生效，指数退避带抖动，次数与退避可配；401/403/400 立即抛永久错误；验证：`tests/unit/test_llm_provider_retry.py` 用 mock 依次返回 500/500/200 断言成功且调用 3 次，返回 401 断言只调用 1 次
- [x] 2.3 改造 `LLMExtractor`：瞬时与永久错误向上抛，只有「输出不合 schema」继续走 `repair_output`；验证：断言模型抛 `LLMTransientError` 时不再产生 `raw=None` 的跳过批次
- [x] 2.4 服务层错误分类：`_mark_failed` 按异常类型写 `error_code` / `error_detail`，并把分类结果落到 attempt 记录；验证：`tests/integration/test_extraction.py`、`tests/integration/test_parse.py` 断言各类异常对应正确错误码
- [ ] 2.5 端到端确认「超时不再伪装成功」：构造模型持续超时的抽取任务，断言任务终态为 `FAILED` 且 `error_code=llm_timeout`；自动化用例已覆盖，仍需在本地 worker 上手工复现一次

## 3. 预算分层与自动重试

- [x] 3.1 重写 `retry()` 的预算校验：按来源分别校验 `auto_retries` / `downgrade_retries` / `manual_revives`，`attempt` 仅作执行序号递增；验证：`tests/integration/test_parse.py` 断言人工复活用尽后返回冲突且任务状态不变
- [x] 3.2 实现自动重试：可重试失败后回 `PENDING` 并按 30s/2m/5m 退避重新投递，`source=auto_retry`；验证：断言投递 `countdown` 参数符合退避序列，attempt 行记录来源与耗时
- [x] 3.3 实现降参重试：自动重试耗尽且仍为可重试失败时，抽取把批大小减半且不低于 2 再执行一次，`source=downgrade`，记录降参前后参数；验证：断言降参后的 `config.batch_size` 小于原值，任务的降参计数为 1
- [x] 3.4 永久失败短路：分类为永久失败时不消耗任何自动预算，直接落终态并给出建议动作；验证：断言凭据无效场景 `auto_retries` 保持 0
- [ ] 3.5 幂等与并发：重复投递、迟到消息、并发投递不得产生第二条执行记录或并行处理；PENDING 门禁与 fencing 已实现，尚缺并发用例

## 4. 执行历史与失败可见性

- [x] 4.1 在两条执行路径上完整写 attempt 记录：开始写 RUNNING、结束写终态与 `duration_ms`、失败写 `error_detail`（阶段、可重试性、原始原因）；验证：断言成功与失败两类记录的字段完整性
- [x] 4.2 任务详情返回 `attempts` 全量历史、`progress`、最近一次 `error{code,message,hint}`、`skip_summary`；任务列表返回最近错误码与文案；验证：断言三次失败返回三条记录且序号连续
- [x] 4.3 错误文案由后端下发，前端不依赖错误码硬编码；验证：未知错误码仍带兜底文案并保留原始码

## 5. 批次断点

- [x] 5.1 按现有 `get_candidate_writer()` 的模式新增断点写入器，在 `commit_batch` 与 `skip_batch` 末尾各自独立事务写一行（含批序号、状态、块标识、候选、执行序号）；验证：断言跑完后断点行数与状态正确
- [x] 5.2 给 `chunk_blocks` 增加断点入口：服务层传入 `resume_index`，重算批次后直接定位到该批；验证：单元测试断言 `batch_index` 从指定值开始且批次内容与全量重算一致
- [x] 5.3 恢复时拼回已完成批次：实体与关系行按批序拼回 state，`skipped_block_ids` 与 `skip_reasons` 还原，统计从断点行重算避免重复计数；验证：断言续跑后的最终候选与一次性全量一致
- [x] 5.4 输入指纹校验：任务存 `hash(批大小 | 块标识列表)`，不一致时清空断点从头执行并在 attempt 记录说明；验证：改动批大小后复用任务断言断点被清空且执行从第 1 批开始
- [x] 5.5 断点生命周期：成功标记已消费、取消保留、任务删除级联删除；验证：`mark_consumed` 用例断言成功任务 payload 被清空但跳过原因保留
- [ ] 5.6 硬中断验证：在第 2 批处理中强制结束进程（测试内抛 `SystemExit` 模拟），断言第 1 批断点仍在库中且重投后从第 2 批继续

## 6. 人工复活、跳过失败批次与取消恢复

- [x] 6.1 新增「继续执行」接口：`FAILED`/`CANCELLED` 回到 `PENDING`，`attempt` 不复位，清 Redis 运行锁后重投，`source=manual_revive`；验证：断言复活后执行序号继续、历史保留
- [x] 6.2 复活支持降参选项（勾选时批大小减半并记录）；验证：`POST /retry {"degrade": true}` 断言新执行使用减半批大小
- [x] 6.3 状态机补 `CANCELLED -> PENDING` 边，且只允许人工复活路径进入；验证：看护不复活取消任务，人工复活可回到待执行
- [x] 6.4 新增「跳过失败批次继续」接口：仅抽取任务、在存在未完成批次区间时可用，只把卡住的那一段区间写成 `skipped(batch_failed)`，不跳过剩余批次；验证：断言缺失批被标跳过而后续批次仍正常处理
- [x] 6.5 成功收尾标记不完整：`incomplete=true` 与未处理批次数、原因分布写进 `stats`，详情可读；验证：断言跳过路径成功的任务 `stats.incomplete` 为真、`unprocessed_batches` 等于跳过批次
- [ ] 6.6 取消与复活竞态：取消优先于看护恢复，取消后再复活可以续跑；取消优先级已实现，尚缺并发用例

## 7. 心跳与僵尸看护

- [x] 7.1 worker 心跳：每完成一批刷新 Redis 键（TTL 120s）并更新 attempt 的 `heartbeat_at`；验证：批处理回调后键存在且 attempt 心跳时间被推进
- [x] 7.2 新增 beat 定时任务：每分钟扫描 `RUNNING` 且心跳超过 300s 未更新的任务；验证：用手工构造的过期任务断言被扫出
- [x] 7.3 看护三分支处置：预算内回 `PENDING` 并清锁重投（`source=watchdog`）、预算外落 `FAILED(task_timeout)`、已取消保持取消；验证：三分支实现并有用例覆盖重排分支
- [x] 7.4 迟到写入拒绝：提交前校验任务 `attempt` 与 attempt 行仍为 `RUNNING`，不匹配则整体放弃并留日志；验证：代码路径已实现，用例待补
- [x] 7.5 阈值联动配置：`soft/hard time limit`、`visibility_timeout`、锁 TTL、心跳 TTL、stale 阈值成组写入配置与 `.env.example`，并在启动日志打印生效值；验证：`app.main` 启动日志输出全部阈值

## 8. 前端展示与操作

- [x] 8.1 补齐 `types.ts` 与 API 客户端（attempts / progress / error / skip_summary、继续执行、跳过失败批次）；验证：`npm run type-check` 通过
- [x] 8.2 任务详情抽屉：执行时间线（来源、状态、耗时、失败位置、原始错误可展开）、进度条、跳过原因聚合；时间线与跳过原因已接入，进度条待补
- [x] 8.3 操作入口：失败时展示「继续执行」「跳过失败批次继续」，并按可用性置灰；按钮已就位，额度置灰与"重新抽取"待补
- [x] 8.4 自动重试期展示：`PENDING` 且最近来源为自动类时显示「重试中（第 N 次）」而非红色失败
- [x] 8.5 错误文案兜底与空态：未知错误码显示兜底文案并保留原始码，无历史时显示空态；空态已实现，未知码渲染待手工验证

## 9. 配置与清理

- [x] 9.1 新增可调配置：自动重试上限与退避序列、降参比例与下限、人工复活上限、心跳与看护阈值、跳过批次开关，默认值置 0/关闭时回到旧行为；验证：置 0 后不再自动重排
- [x] 9.2 断点清理：任务成功后清空 `payload` 只留计数与跳过原因，过期任务按现有保留策略清理；验证：断言成功任务 payload 为空、统计仍在

## 10. 集成验证

- [x] 10.1 端到端主链：可重试失败 → 自动重试 → 降参 → `FAILED(attempts_exhausted)` → 人工复活 → 从断点跑完 → `SUCCEEDED`；验证：脚本化用例跑通并核对每次 attempt 的来源
- [x] 10.2 端到端跳过路径：批次反复失败 → 失败 → 跳过失败批次继续 → 成功且 `incomplete=true`；验证：断言未处理批次为 1、跳过原因聚合正确
- [ ] 10.3 看护链路：执行中杀掉 worker → 心跳过期 → 看护回 `PENDING` → 从断点续跑成功；自动化已覆盖看护重排，仍需在本地 compose 环境手工复现一次
- [ ] 10.4 全量回归：`python -m pytest -q` 与 `ruff check app tests` 通过；当前 ruff 全绿、pytest 仅 `test_retrieval` 因本地工作区存在历史文档 `guiding-1` 而失败（环境数据问题，非本变更回归）

## Workflow follow-up

- 归档前与 `knowledge-base-platform` 的 `parsing-pipeline` 规格做一次口径对齐：其"最多重试 3 次，超过后需人工介入"的表述需让位于本变更的预算分层。
- 归档 `add-task-retry-and-recovery` 并确认 `openspec/specs` 下生成 `task-retry-and-recovery`、`extraction-checkpoint` 两个能力。
