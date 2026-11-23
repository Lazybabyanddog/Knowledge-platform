# Design

## Context

动机见 `proposal.md`。约束来自现有实现，均已核对：

**两个任务服务同构。** `ParseTaskService.execute` 与 `ExtractionTaskService.execute` 都是：抢占 Redis 锁（`SET NX EX 1800`，键 `parse:run:{id}` / `extract:run:{id}`）→ 状态机迁移到 `RUNNING` → `attempt += 1` → 跑流水线 → 落库 → `commit`。异常一律 `except Exception` 兜住，`rollback` 后 `_mark_failed`，**不向 Celery 抛出**。

**Celery 层没有任何重试能力。** `tasks.py` 全部 `max_retries=0`，`celery_app.py` 只有 `task_acks_late=True` + `worker_prefetch_multiplier=1`：没有 `task_time_limit`、没有 beat、`visibility_timeout` 用 Redis 默认 3600s。已经在 Redis 里看到 `celery-task-meta-*` 全是 `SUCCESS`，而库里任务是 `FAILED` —— 说明"加重试"如果只改 Celery 参数是无效的。

**`attempt` 是唯一计数，且被三种来源共享。** `retry()` 用 `task.attempt >= settings.parse_max_attempts`（=3）挡人，一旦自动重试复用这个计数，人工重试就会被自动重试吃掉。

**状态机已有复活边。** `ParseTaskStatus.FAILED -> PENDING`、`ExtractionTaskStatus.FAILED -> PENDING` 都已存在，`retry()` 走的就是它。`CANCELLED` 是终态，没有出边。

**抽取图是内存累积、末尾一次落库。** `entities` / `relation_rows` 只在 graph state 里累积，`persist_candidates` 一次性写 Neo4j。但三个性质让断点可行：

- `chunk_blocks` 从 `blocks` + `batch_size` 确定性重算批次，同一任务任何时刻结果一致 → `batch_index` 可以当游标；
- `commit_batch` 的产出是自包含的（关系用 `class_code:normalized_name` 作为端点 key，不引用内存对象）；
- `dedupe_candidates` 用 `uuid5(task_id, class_code, name)` 生成实体 id，跨执行稳定。

**模型错误被静默降级。** `LLMExtractor.extract` 捕获 `LLMError` 后返回 `raw=None, error=...`，`schema_validate` 判成问题 → `skip_batch` → 任务 `SUCCEEDED`，`stats.skip_reasons` 里躺着"模型调用超时"。这是"页面看不到失败原因"的根因。

**现有配置项**：`parse_max_attempts=3`、`extract_max_attempts=3`、`extract_batch_size=8`、`extract_max_blocks=2000`、`llm_timeout_seconds=120`。

## Goals / Non-Goals

**Goals:**

- 失败之后始终有出口，且出口的成本与语义对用户可解释；
- 恢复不重复烧钱：抽取从未完成的批次继续；
- 失败原因可以定位到"第几批、哪种错、试了几次"，而不是一句话；
- 执行者消失时由系统收拾现场，而不是把文档版本锁死。

**Non-Goals:**

- 不引入 `RETRYING` 状态和"30 秒后重试"倒计时（第二期，收益主要在前端表现力）；
- 不给解析做批次断点（解析是单次确定性流水线，没有可切分的模型调用，重跑便宜）；
- 不改动候选知识的审核流程、实体合并与图谱写入语义；
- 不做"自动部分成功"：把不完整结果入库必须是用户的显式选择；
- 不做跨任务的候选复用与去重。

## Decisions

### D1 错误分层：三个边界，而不是一个 `except`

```
L0  单次调用（进程内，秒级）
    chat_json: 429 / 5xx / timeout / 连接重置
        -> 退避 + 抖动重试（默认 3 次）
        -> 仍失败: raise LLMTransientError(code="llm_timeout" | "llm_unavailable")
    401 / 403 / 400 / 模型不存在
        -> 立即 raise LLMPermanentError(code="llm_rejected")
    输出不是合法 JSON 或不合 schema
        -> 不抛，交给 repair_output

L1  抽取器（批次级）
    LLMExtractor 不再把 LLMError 吞成 raw=None
    -> 瞬时/永久错误直接向上抛
    -> 只有"输出不合规"继续走 repair -> 仍不合规才 skip_batch

L2  服务层（任务级）
    分类后写 attempt 记录，再决定：自动重试 / 降参 / 失败终态
```

错误码表（对外稳定，前端据此渲染文案）：

| 错误码 | 类别 | 来源 | 可自动重试 |
|---|---|---|---|
| `llm_timeout` | 可重试 | 模型调用超时 | 是 |
| `llm_unavailable` | 可重试 | 429 / 5xx / 连接失败 | 是 |
| `llm_rejected` | 永久 | 凭据无效、请求被拒 | 否 |
| `neo4j_unavailable` | 可重试 | 图库写入失败 | 是 |
| `bad_input` | 永久 | 文件损坏、格式不支持、解析无内容块 | 否 |
| `ontology_missing` | 永久 | 触发时冻结的本体已失效 | 否 |
| `task_timeout` | 可重试 | 任务级超时或看护判死 | 是 |
| `attempts_exhausted` | 终态 | 预算用尽 | 否 |
| `cancelled` | 终态 | 用户取消 | 否 |
| `internal_error` | 永久 | 未分类缺陷 | 否 |

**为什么不用 Celery `autoretry_for`**：service 已经把异常吃掉，异常根本上不去；而且重试决策要看预算来源、断点位置与降参状态，这些 Celery 都看不到。Celery 只负责"投递一次"，重试决策留在服务层。

### D2 预算模型：一个序号 + 三个额度

```
attempt          执行序号，只增不复位，同时充当写入版本号（fencing）
auto_retries     自动重试已用，上限 3
downgrade_retries 降参重试已用，上限 1
manual_revives   人工复活已用，上限 2
```

- 任务表上冗余这四个计数（列表页要展示，避免每行聚合 attempt 表）；attempt 表是真相源，两者在同一事务里更新。
- `attempt` 不复位是刻意的：它是"这次执行属于第几版"的凭据（见 D6 的迟到写入拒绝），也是页面上"第几次"的唯一来源。
- 降参只对抽取生效（`batch_size` 减半，下限 2）。解析没有可降参数，自动预算用尽直接落 `attempts_exhausted`。

预算流转：

```
execute 开始
  attempt += 1
  ├─ 成功                    -> SUCCEEDED
  ├─ 永久失败                -> FAILED(错误码)                      [人工复活/重新触发]
  └─ 可重试失败
       ├─ auto_retries < 3   -> PENDING  (退避 30s/2m/5m, source=auto_retry)
       ├─ downgrade < 1      -> PENDING  (batch_size 减半, source=downgrade)
       └─ 否则               -> FAILED(attempts_exhausted)
                                   ├─ 继续执行   -> PENDING (source=manual_revive)
                                   └─ 跳过失败批 -> PENDING (source=skip_batches)
```

### D3 自动重试沿用 `FAILED -> PENDING`，不引入新状态

自动重试走 `RUNNING -> FAILED -> PENDING`，复用现有边，状态机、审计、前端映射都不用动。代价是任务会短暂显示"失败"，并且 `finished_at` 被写过一次。

处理方式：`finished_at` 在下一次执行开始时清空；前端按 `attempts` 的最新一条 `source` 区分"系统正在重试（第 3 次）"与"人工触发"。第二期如果要做倒计时，再加 `RETRYING`，那时改的是展示而不是恢复逻辑。

**备选与否决理由**：加 `RUNNING -> PENDING` 边或 `RETRYING` 状态，需要同步改状态机白名单、前端状态映射、审计动作枚举、`find_active` 判定（`PENDING`/`RUNNING` 之外是否算活跃），改动面与收益不成比例。

**唯一要加的边是 `CANCELLED -> PENDING`**，且只允许人工继续执行走这条边：看护永不恢复已取消的任务（取消优先），但用户取消后想接着跑是合理诉求，而 `CANCELLED` 当前是死胡同。这条边不引入新状态，只让"取消"从终态变为"可人工恢复的终态"。

### D4 批次断点：一张表 + 一个指纹

```sql
extraction_batch_checkpoint
  id                uuid  pk
  extraction_task_id uuid  fk -> extraction_task(id) on delete cascade
  batch_index       int    not null
  status            varchar(16)   -- committed | skipped
  block_ids         uuid[]        -- 该批覆盖的块，便于定位与部分结果展示
  payload           jsonb         -- {entities: [...], relation_rows: [...]}
  attempt_no        int           -- 写这条断点的执行序号（溯源）
  created_at        timestamptz
  unique (extraction_task_id, batch_index)   -- 即 fencing：先到先得
```

**每条唯一约束就是并发保护**：`INSERT ... ON CONFLICT DO NOTHING`，旧执行与新执行同时写第 188 批时不会互相覆盖，也不需要额外加锁。

**写入点**：`commit_batch` 与 `skip_batch` 的末尾，通过注入的 checkpoint writer 完成（沿用现有 `get_candidate_writer()` 的注入模式，图节点保持"纯函数 + 注入副作用"）。每次写入独立事务提交，不并入任务主事务——这是"进程被硬杀也不丢进度"的前提。

**恢复**：

```
execute
  -> _load_blocks (按 seq, 上限 extract_max_blocks)
  -> chunk_blocks: 重算 batches，按 resume_index 定位
  -> 读 checkpoint:
       已提交批 -> entities / relation_rows 拼回 state
       已跳过批 -> skipped_block_ids / skip_reasons 拼回
       stats 计数从行里重算（不能叠加旧 stats，否则重复计数）
  -> classify_blocks ... -> dedupe_candidates -> persist_candidates
```

`chunk_blocks` 需要一个 `resume_index` 入口（由服务层传入），其余节点不改签名。

**输入一致性**：任务上存 `checkpoint_fingerprint = hash(batch_size | 块 id 列表)`。重算出的指纹不同则整表清空、从第 1 批重来，并在 attempt 记录里写明"输入已变化，放弃断点"。这挡住了"解析产物被替换后旧断点混进新结果"的情况。

**为什么不把进度塞进 `extraction_task.stats`（JSONB）**：并发写会整块覆盖、无法按批查询、也无法在失败时展示部分候选。**为什么不放 Redis**：重启即丢，恰好在最需要它的时候不在。

**生命周期**：成功 → 断点保留但标记 `consumed_at`，不再参与恢复（保留是为了"哪些块产出了哪些实体"的溯源）；取消 → 保留，供复活续跑；任务删除 → 级联删除。

### D5 执行历史：两张同构表

```
parse_task_attempt                extraction_task_attempt (新增)
  parse_task_id                    extraction_task_id
  attempt_no            unique     attempt_no            unique
  status                           status
  quality_score                    -- (抽取无质量分)
  stats  jsonb                     stats  jsonb
  error_code                       error_code
  error_message                    error_message
  error_detail jsonb  (新增)        error_detail jsonb
  source varchar(24)  (新增)        source varchar(24)
  duration_ms int     (新增)        duration_ms int
  started_at / finished_at         started_at / finished_at
```

`source` 取值：`manual` / `auto_retry` / `downgrade` / `manual_revive` / `skip_batches` / `watchdog`。

`error_detail` 结构（只放能定位问题的字段，不放堆栈）：

```json
{
  "stage": "extract_entities",
  "batch_index": 187,
  "block_seq": [1498, 1505],
  "cause": "llm_timeout",
  "call_attempts": 3,
  "last_call_ms": 120000,
  "retryable": true,
  "resume_from": 187
}
```

`progress` 快照也放 `stats`（`batches_total` / `batches_done` / `batches_skipped` / `resume_from`），这样"哪次有进展"在列表里一眼可见，不需要 join 断点表。

历史只增不改：执行进入终态后不再更新该行；`_mark_failed` 只写当前 attempt 行，不碰旧行。

### D6 看护：心跳判定 + 三分支处置

```
worker 每完成一批 -> SET aip:worker:{hostname} EX 120
                  -> UPDATE attempt SET heartbeat_at = now()

beat 每 60s -> SELECT ... WHERE status='running' AND heartbeat_at < now() - 300s
   ├─ 判死时用户已取消        -> 保持 CANCELLED（取消优先）
   ├─ 永久失败类别            -> FAILED(error_code)
   ├─ auto_retries < 3        -> PENDING + DEL 锁 + 重投 (source=watchdog)
   └─ 否则                    -> FAILED(task_timeout)
```

四个阈值必须联动，单独调任何一个都会出问题：

| 参数 | 建议值 | 不一致的后果 |
|---|---|---|
| `llm_timeout_seconds` | 120（现有） | 单次调用上限 |
| `extract_soft_time_limit` | 3600s | 有断点，超时不再致命；无上限则僵尸永不"超时" |
| `extract_hard_time_limit` | 3900s | 硬杀后靠看护 |
| `visibility_timeout` | 5400s | 小于硬上限 → broker 提前重投，投了也白投 |
| `lock_ttl` | 5400s（或每批续期） | 小于任务耗时 → 锁中途失效 → 双执行 |
| `heartbeat_ttl` | 120s | 误杀 |
| `stale_threshold` | 300s | 误杀 / 发现太慢 |

**迟到的旧执行必须被拒绝**：写入前校验 `task.attempt == 本次执行开始时的 attempt` 且 attempt 行仍为 `RUNNING`；不满足则整体放弃提交（日志留痕）。断点的唯一约束是第二道保险。

### D7 失败可见性：后端给文案，前端给结构

详情响应新增：

```
attempts: [{ attempt_no, source, status, started_at, finished_at, duration_ms,
             progress: {...}, error_code, error_message, error_detail }]
progress: { batches_total, batches_done, batches_skipped, current_batch }
error: { code, message, hint }        // 最近一次
skip_summary: [{ reason, count }]
```

**错误文案与建议动作放后端**（`error_catalog`），一次改所有端生效，前端只做兜底：

```
llm_timeout        模型调用超时            建议：稍后继续执行，或降低批大小后重试
llm_unavailable    模型服务不可用          建议：检查模型服务状态与额度
llm_rejected       模型拒绝请求            建议：检查 API Key 与模型名
neo4j_unavailable  图库写入失败            建议：检查图库连接后继续执行
bad_input          输入不可处理            建议：检查文档格式与解析结果
ontology_missing   本体已变更              建议：重新触发抽取
task_timeout       执行超时                建议：从断点继续执行
attempts_exhausted 重试预算耗尽            建议：继续执行、跳过失败批次或修改输入后重新触发
```

页面结构（文档详情页 → 任务行 → 详情抽屉）：

```
+------------------------------------------------------------+
| 抽取任务 3f9c1a2b                          [失败]           |
| 250 批 · 已完成 187 · 跳过 9 · 进度 74.8%                   |
| [继续执行] [跳过失败批次继续] [重新抽取]                     |
+------------------------------------------------------------+
| #4 自动重试  失败  13:02-13:19  17m02s  进度 187/250 (+0)   |
|    llm_timeout · 模型调用超时（单次 120s，重试 3 次）        |
|    位置：第 188 批 · 块 seq 1498-1505                       |
| #3 自动重试  失败  12:41-12:58  17m19s  进度 187/250 (+0)   |
| #2 自动重试  失败  12:20-12:38  17m55s  进度 187/250 (+90)  |
| #1 手动触发  失败  12:01-12:19  18m10s  进度  97/250 (+97)  |
+------------------------------------------------------------+
| 跳过原因：页眉页脚 6 · 内容过短 2 · 输出不合规 1             |
+------------------------------------------------------------+
```

`find_active` 仍然只把 `PENDING` / `RUNNING` 当活跃，`FAILED` 不挡重新触发，这条不变。

### D8 "跳过失败批次继续"的实现路径

不是新状态，而是"带跳过指令的一次执行"：

1. 校验：任务为抽取、状态为 `FAILED`、存在 `batch_index` 连续且未完成的区间；
2. 把未完成区间内的批次写入断点表，`status='skipped'`、原因为 `batch_failed`（只写"卡住的那一段"，不是"剩下的全部"，剩余批次仍要正常抽取）；
3. 任务回 `PENDING`，`source=skip_batches`，清锁后重投；
4. 执行从第一个未完成批开始，跑完剩余批次 → 成功；
5. 成功时 `stats.incomplete=true`、`stats.unprocessed_batches=N`，详情页显示"250 批中 1 批未处理"。

第 2 步的"只写卡住的那一段"是关键：如果把"第 188 批以之后全部"都标成跳过，就退化成"跳过一切"。

**为什么不新增 `PARTIAL` 状态**：状态机、前端状态映射、审计枚举、`find_active` 四处都要改，收益只是一个标签；用 `stats.incomplete` + 明细可以表达得更细。

## Risks / Trade-offs

- **每批一次独立提交（250 次小事务）** → 相对每批数秒到数十秒的模型调用可忽略；若压测显示瓶颈，退化为"每 N 批一次"并接受恢复粒度变粗（N 记在指纹里，避免不同粒度混用）。
- **断点 payload 体积**（250 批 × 数十 KB，任务级可达数 MB）→ 成功后清 `payload` 只留计数；`parse_block_retention_days=90` 的清理任务同步清理过期断点。
- **看护误杀**（worker 未死但长时间卡在单次调用）→ 心跳由批次驱动、阈值 300s 远大于 `llm_timeout_seconds=120`；再加 fencing 拒绝迟到写入，误杀的代价是"重跑一段",不是"数据错乱"。
- **降参掩盖配置错误**（把"根本跑不动"变成"慢慢跑成功"）→ 降参只给 1 次，且执行历史里明确标注 `source=downgrade` 与前后参数；页面同时保留"重新触发"入口。
- **错误码前后端耦合** → 文案由后端下发，前端只维护兜底映射与图标；新增错误码不需要发版前端即可展示。
- **与 `knowledge-base-platform` 的规格重叠**：`parsing-pipeline` 中"限制单个任务最多重试 3 次，超过后需人工介入"与本变更的预算分层不一致。归档顺序上先归档 `knowledge-base-platform`，再由本变更的规格覆盖该条口径；两边都未归档前以本变更为准。
- **自动重试期间的"失败"闪现** → 前端在 `PENDING` 且 `attempts` 最新 `source` 为自动类时展示"重试中（第 N 次）"，不用红色失败样式。

## Migration Plan

1. **迁移脚本**：新增 `extraction_task_attempt`、`extraction_batch_checkpoint`；`parse_task_attempt` 加 `error_detail` / `source` / `duration_ms`（nullable 或带 `server_default`）；`parse_task` 与 `extraction_task` 加 `auto_retries` / `downgrade_retries` / `manual_revives` / `checkpoint_fingerprint` / `incomplete`（默认 0/false）。
2. **回填**：`manual_revives=0`、`auto_retries=0`；历史 `parse_task_attempt` 的 `source` 统一填 `manual`，`duration_ms` 由起止时间推导，不可推的空着。
3. **开关**：`task_auto_retry_max_attempts` 默认 3，置 0 即回到"无自动重试"的旧行为；`task_watchdog_enabled` 默认开，可单独关停 beat。
4. **上线顺序**（每步可独立回滚）：
   - 第 1 步 错误分类 + 错误码表（不改状态机、不改预算）→ 先让失败能显形；
   - 第 2 步 预算分层 + 自动重试 + 人工复活 + 执行历史；
   - 第 3 步 批次断点 + 恢复 + 进度展示；
   - 第 4 步 降参重试 + 跳过失败批次；
   - 第 5 步 心跳 + 看护。
5. **回滚**：断点表可直接停用（恢复退化为全量重跑，正确性不受影响）；看护停 beat 即消失；预算置 0 即回到手动重试。数据迁移不回滚（新增列可留）。

## Open Questions

- 心跳键 TTL 与 `stale_threshold` 的最终取值需要按真实批处理耗时分布压测确认（当前 120s / 300s 是按 `llm_timeout_seconds=120` 推导的保守值），可在实现阶段调整，不影响规格与任务拆分。
