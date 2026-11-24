# Design

## Context

现状（见 proposal.md 的动机，这里只列影响设计的约束）：

- `RetrievalService.search()` 一次调用只处理一个查询：单次 `embed_texts([q])` + 单条 `cosine_distance` SQL，过滤条件（workspace / published / deleted_at / current version / 密级 / doc_type）与排序写在同一条语句里。多查询改写必须在不破坏这套过滤语义的前提下扩展。
- 没有 rerank 模型配置；`infra/llm` 只提供同步的 `chat_json`（带重试与 JSON 修复）与 `embed_texts`，流式只有 `astream_chat`。
- 图谱侧 `EntityRepository.list_entities(workspace_id, keyword=..., limit=...)` 已按 `name` / `normalized_name` 做模糊匹配，实体有 `normalized_name`、`identity_key` 与合并后的别名，可直接作为实体链接的候选来源；Neo4j 不可用时该能力不可用。
- 前端已有统一的 `http` 封装与 SSE 解析器，`AskView` 是纯 `question + doc_type + top_k` 的单轮问答；路由菜单由 `appRoutes` 元信息驱动。
- 本次不改变 `/retrieval/search` 与 `/retrieval/ask` 的行为，新增的是一套可独立运行的改写内核 + 一个只读实验台。

## Goals / Non-Goals

**Goals:**

- 一次运行产出完整 `RewriteTrace`，其中每个数字都能由真实检索复算。
- 内核与实验台解耦：实验台只依赖内核的 Trace，未来接入问答链路时不需要复制任何逻辑。
- 策略可插拔：新增或启用策略只改注册表与执行器，不改路由与编排。

**Non-Goals:**

- 不做 Rerank 模型（融合阶段只有去重 + RRF）。
- 不做运行历史持久化与回归评测集（下一阶段接入问答链路时再考虑）。
- 不做跨语言改写、不做查询缓存、不做 HyDE/Step-back 的模型调用。
- 不改动文档、索引、审批与审计数据。

## Decisions

### 1. 内核 + Trace 作为唯一出口，实验台只是渲染器

`QueryRewriteService.run(principal, request) -> RewriteTrace`。实验台路由直接把 Trace 序列化返回；下一步接入问答时，`/ask` 拿同一个 Trace 只消费 `fusion.context`。

**为什么不先做"能直接影响检索"的开关**：现在没有任何对照数据能说明改写有效，直接接入问答会让"变好还是变坏"无法归因。Trace 先行，接入就是换一个消费方。

### 2. 每次运行强制跑对照组

baseline 臂用原始 query 检索，rewrite 臂用改写后的主查询 + 扩展查询。两臂共用同一个 `_base_stmt()`（把现有过滤条件抽出来），保证只有查询文本不同。

成本：1 次 embed + 1 条 SQL，不消耗 LLM。收益：`lift = rewrite.top1 - baseline.top1` 是真实测量值，二阶段触发条件也从"猜"变成"测"。

### 3. 批量向量化 + 逐条 SQL

`embed_texts` 本来就是批量接口（内部按 `embedding_batch_size` 分片）。改写组有 N 条查询时：一次 `embed_texts([q1..qn])`，然后 N 条 `_base_stmt()` 语句。

**不做的替代方案**：把所有查询的向量拼进一条 SQL 做 `UNION ALL` 或 `LATERAL`——SQL 复杂度高、`hnsw.ef_search` 的会话级设置对每条扫描的收益也不变，收益不足以抵消可读性损失。

### 4. Router 用纯规则决策，模型只产候选

模型输出的 `candidate_strategies` 只进入解释与排序，不直接触发执行；执行集 = 规则命中 ∩ Allowlist，再按固定优先级截断到 `budget`。

优先级常量：`context > entity > semantic > decompose > multi`（先解析指代，再对齐实体，再整理表达，最后才展开）。依赖表达为"该顺序即执行顺序"，不使用通用 DAG 拓扑排序——策略数固定且依赖是链式的，通用编排器在这里是过度设计，反而让决策不可解释。

### 5. 策略执行分两类语义

- 文本改写类：`context → entity → semantic` 串行，后者的输入是前者输出。
- 召回扩展类：`decompose`、`multi` 不改主查询，只产出额外查询集，且各自保留"子问题/变体 → 命中片段"的映射。

扩展类产生的查询计入 `max_queries`；文本改写类不计入检索次数（它们只花 LLM 调用）。

### 6. 实体标准化：先图后模型

```text
抽取疑似实体片段 → list_entities(keyword=..., workspace 内)
  0 候选 → skipped（不调模型）
  1 候选 → 直接采用，confidence=1
  ≥2 候选 → 1 次 chat_json 在候选间选择，输出 entity_id + confidence
```

**为什么不直接让模型输出标准名**：模型凭空生成的规范名对不上图谱主键，既无法解释也无法在后续图谱查询里使用；先图后模型让这一步的结果天然可回溯到实体 id。

Neo4j 不可用时该策略记为"未启用（图谱不可用）"，运行继续。

### 7. 质量评估只用代理指标，公式写死

```text
top1        = 该臂最高相似度
above_tau   = 相似度 ≥ quality_threshold 的片段数
docs        = 命中片段覆盖的不同文档数
verdict     = sufficient 当且仅当 above_tau >= min_evidence 且 top1 >= quality_threshold
lift        = rewrite.top1 - baseline.top1
```

没有标注数据，任何"绝对质量分"都是编的。因此**只报可观测量 + 相对增益**，并在 UI 上明确标注为代理指标。`min_evidence` 默认 2，来自配置。

### 8. 融合：chunk_id 去重 + RRF

按 `chunk_id` 归并多路命中，记录命中来源列表；排序用 RRF（`k=60`，`score = Σ 1/(k + rank_i)`）。对外展示的 `score` 仍取该片段在各路中的最大余弦相似度——因为用户理解的是"相关度"，不是融合分；融合分只用于排序。

### 9. 预算与超时用"截止时间 + 计数器"，不是取消机制

一次运行开始时记录 `deadline = now + query_rewrite_timeout_seconds`。每次派发策略/检索前检查：剩余预算、剩余查询数、是否已过 deadline。任一不满足则停止派发，把未执行策略标记为被预算拦截或降级。

**为什么不真取消**：`chat_json` 是同步阻塞调用，Python 侧无法可靠中断已发出的请求；与其做假取消，不如在派发前控制，超时后回退到已完成的 baseline 结果（HTTP 200 + `degraded`）。

### 10. 策略注册表数据驱动

```text
StrategySpec(code, label, kind=text|expand, requires_llm, depends_on, mutex, priority, enabled)
```

`hyde` / `stepback` 以 `enabled=False` 注册，因此它们照样出现在决策表与前端清单里，理由统一为"当前版本未启用"，且不会被派发。这是让"未启用策略可见但不执行"落在代码结构里，而不是散落的 if。

### 11. 同步实现，端点走线程池

`chat_json` 是同步的，编排逻辑保持同步 `def` 端点（FastAPI 自动放入线程池），避免 async 里包 `run_in_threadpool` 的层层嵌套。SSE 不在本次范围内。

### 12. 实验台不做审计写入

`/ask` 会写 `RETRIEVAL_ASK` 审计（问题摘要 + 命中数）。实验台一次调参可能连跑十几次，写审计既污染审计流水又增加写入；它本身是只读诊断面，与 `/search` 一致不写审计。

## Risks / Trade-offs

- **[改写引入未经证实的事实]** → 提示词约束"只重组表达、不新增实体与数字"，并在 Trace 中并排展示改写前后文本；用户能立刻看出偏差。实体标准化只做映射不做生成。
- **[规则路由不如模型分类"聪明"]** → 接受：可解释和可调优先；规则信号（指代线索、复合连词、口语词、别名候选）都由真实命中产生，误判时可以定位到具体规则再补。
- **[对照组使每次运行成本翻倍（检索侧）]** → 检索侧只是 1 次 embed + 1 条 SQL，相对 LLM 调用可忽略；这个对照是"有没有用"的唯一证据，不能省。
- **[代理指标不等于答案质量]** → 用词上严格区分"召回质量（代理）"与"答案质量"，UI 不做"更好/更差"的绝对断言，只报增益与阈值对比。
- **[串行 LLM 调用拖慢单次运行]** → 文本改写类最多 3 次串行调用；用 `max_llm_calls=4` 与 `20s` 截止时间封顶，超时降级为 baseline。
- **[实验台暴露内部提示词与规则]** → 只回传策略名、规则信号与理由，不回传系统提示词全文；页面沿用 `document:read` 权限。

## Migration Plan

纯增量，无数据库迁移、无索引变更：

1. 新增配置项（默认值即可运行，不改 `.env` 也能启动）。
2. 新增 `/retrieval/rewrite-lab/run` 路由与前端 `/rewrite-lab` 页面，不影响既有路由。
3. 回滚 = 删除新增路由与页面文件；`RetrievalService` 只新增 `_base_stmt()` 抽取（行为等价的内部重构，`/search` 结果不变）。
4. 上线前用同一批问题跑三组对照（不启用改写 / 只开语义改写 / 全开），把 `lift` 记录在变更记录里，作为接入问答链路的基线。

## Open Questions

无。后续阶段的入口（把 Trace 接进 `/ask`）取决于本次积累的对照数据，不需要现在决定参数。
