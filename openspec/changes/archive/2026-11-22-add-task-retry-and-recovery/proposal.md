# Proposal

## Why

解析与抽取任务失败后没有可用出口。当前 `attempt` 同时充当"执行序号"和"重试预算"，自动重试与人工重试共用同一个上限 3，一旦耗尽，界面上"重试"按钮点下去必定返回 409，唯一出路是重新触发一个新任务——历史被切断、抽取全量重跑、token 再烧一遍。更糟的是耗时性的失败根本不会变成"失败"：LLM 超时被 `LLMExtractor` 吞掉并降级为 `skip_batch`，任务最终以 `SUCCEEDED` 结束但 0 实体，用户看不到任何失败原因。抽取任务还有第二重浪费：250 个批次的结果只存在内存里，第 188 批失败意味着前 187 批的调用全部白花。

## What Changes

**错误分类与上抛**

- 把模型调用错误分为可重试（超时 / 429 / 5xx / 连接重置）、永久（401 / 400 / 模型不存在）与内容问题（页眉页脚 / 过短 / 输出不合 schema）三类，只有前两类升级为任务级失败。
- `chat_json` 增加进程内重试（指数退避 + 抖动），抖动在进程内消化，不惊动任务状态。
- 抽取不再因为模型整体不可用而"成功但 0 实体"。

**预算分层与自动重试**

- 拆分 `attempt`（执行序号，只增，兼作写入版本号）、`auto_retries`（自动重试，上限 3）、`downgrade_retries`（降参重试，上限 1）、`manual_revives`（人工复活，上限 2）。
- 自动重试沿用现有 `FAILED -> PENDING` 状态迁移边，退避 30s / 2m / 5m。
- 自动重试耗尽后自动降参重试一次：抽取把 `batch_size` 减半（下限 2）；解析没有可降参数，不占用降参预算。
- 降参仍失败才落 `FAILED(attempts_exhausted)`。

**人工复活（第三种出口）**

- 失败任务提供"继续执行"：从断点续跑，不清零 `attempt`，不超过人工复活预算。
- 失败任务提供"跳过失败批次继续"：把反复失败的批次记为空转并注明原因，跑完剩余批次，任务以"部分完成"收尾并在页面标注未处理批次。

**批次断点**

- 抽取在每批 `commit_batch` / `skip_batch` 后独立提交一行批次结果，恢复时按 `batch_index` 续跑，已完成批次的结果从库中拼回供全局去重使用。
- 断点同时是进度来源：页面可显示 `187/250`、失败位置（批次与块序号）、以及已产出但未入库的候选数量。

**执行历史与失败可见性**

- 抽取新增与解析同构的 attempt 历史表；两张表都补充 `source`（手动 / 自动重试 / 降参 / 人工复活 / 看护恢复）、`progress`、`error_detail`、`duration_ms`。
- 错误码枚举化并提供中文文案与建议动作；任务详情返回历次执行的耗时、进度增量、失败阶段、失败位置与原始错误。

**僵尸看护**

- worker 心跳落 Redis，执行过程中按批次刷新；beat 周期性扫描 `RUNNING` 且心跳过期的任务。
- 判死后的处理分三种：预算内回 `PENDING` 并重新投递（无声续跑）、预算外落 `FAILED(timeout)` 交给人工、明确输入错误直接落 `FAILED(permanent)`。
- 复活与看护恢复必须清除运行锁，并靠 `attempt` 作为写入版本号防止假死进程交叉写入。

## Capabilities

### New Capabilities

- `task-retry-and-recovery`: 解析与抽取任务的错误分类、预算分层、自动重试与降参、人工复活与跳过失败批次、僵尸看护、执行历史与失败原因可见性。
- `extraction-checkpoint`: 抽取的批次级断点、断点恢复语义、进度与部分结果的可观测性。

### Modified Capabilities

无。`openspec list --specs` 当前为空，`parsing-pipeline` 与 `knowledge-graph` 仍只存在于进行中的 `knowledge-base-platform` 变更里；本变更以新能力承载任务恢复语义，归档 `knowledge-base-platform` 时需要与其"解析失败重试"一节做一次口径对齐（其"最多重试 3 次"表述将被本变更的预算分层取代）。

## Impact

- **数据模型**：新增 `extraction_task_attempt`、`extraction_batch_checkpoint`；`parse_task_attempt` 与 `extraction_task` 增加预算、进度、错误明细字段；需要 Alembic 迁移与历史 `attempt` 数据回填。
- **服务层**：`parse_task_service` / `extraction_service` 的错误边界、`retry` 预算校验、`_mark_failed` 错误分类全部重写；新增 revive / skip-failed-batch / resume 三个入口。
- **流水线**：`extract/nodes.py` 的 `chunk_blocks` 支持断点起始、`commit_batch` 与 `skip_batch` 增加断点写入、`dedupe_candidates` 消费从断点拼回的候选；`extract/extractor.py` 不再把模型错误降级为 skip。
- **Worker**：Celery `max_retries` 语义、`task_time_limit` / `soft_time_limit`、`visibility_timeout`、锁 TTL 与看护阈值需要联动设定；新增 beat 定时任务与 worker 心跳。
- **API**：任务详情返回执行历史与进度；新增继续执行、跳过失败批次、查询批次进度接口；错误码与前端文案映射。
- **前端**：文档详情页的解析/抽取任务表增加详情抽屉（执行时间线、进度、失败位置、跳过原因聚合）与三个操作入口。
- **配置**：新增自动重试预算、退避、降参比例、心跳与看护阈值、跳过批次的开关项。
- **兼容性**：`attempt` 语义从"重试计数"变为"执行序号"，现有 `retry` 接口的 409 行为改变，属于对外可观察行为变更。
