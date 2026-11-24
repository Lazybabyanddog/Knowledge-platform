# Proposal

## Why

现有检索链路（`RetrievalService.search` + `/retrieval/search`、`/retrieval/ask`）只用用户原始查询做一次向量召回，遇到口语化表达、跨轮指代、长复合问题时召回质量明显下降；`add-rag-retrieval` 当时把查询改写列为 Non-Goals，这条链路至今没有可调优的入口。

但改写本身是高风险动作：它可能把原意改偏、把幻觉写进查询，而现在系统既无法回答"这次改写到底有没有让召回变好"，也没有任何中间过程可看。所以先建可观测的实验台——用真实 LLM 与真实检索跑通"配置策略 → 改写 → 检索 → 评估"闭环，让改写效果可被度量、策略可被调参；等有了对照数据，再把同一套内核接进问答链路。

## What Changes

- 新增查询改写引擎：**查询理解 → 策略路由 → DAG 执行 → 双路检索 → 质量评估 → 条件二阶段补充 → 融合**，一次运行产出一份完整的 `RewriteTrace`。
- 首批真实落地 **5 个策略**：`context`（上下文改写）、`semantic`（语义改写）、`entity`（实体标准化）、`decompose`（问题拆解）、`multi`（Multi-Query）。`hyde`、`stepback` 只在策略清单中登记为"未启用"：面板可见、决策可解释，但执行器返回未启用原因，不产生 LLM 调用。
- **策略两类别执行语义**：文本改写类（`context`→`entity`→`semantic`）串行串联，后者接收前者输出；召回扩展类（`decompose`、`multi`）并联产生额外查询集，各自独立检索。不存在"全部并行调用"。
- **对照组检索**：每次运行除改写查询外，固定用原始查询再检索一次作为 baseline，不额外消耗 LLM；评估产出 `top1`、`above_tau`、`distinct_docs` 与相对 baseline 的 `lift`，指数为真实余弦相似度而非模拟值。
- **实体标准化接图谱而非让模型猜别名**：先用 `EntityRepository.list_entities(keyword=...)` 取候选（`name` / `normalized_name` 匹配），0 候选跳过、单候选直接采用、多候选才花 1 次 LLM 消歧。
- **预算与降级硬约束**：`max_llm_calls`、`max_queries` 超限即停止派发新策略；任一策略失败记录为 `degraded` 并继续；整体超时回退到 baseline 结果，运行仍然成功返回。
- 新增 `POST /api/v1/retrieval/rewrite-lab/run`，返回 `RewriteTrace`（含理解结果、逐策略决策与理由、改写前后文本、双路命中、评估结论、二阶段动作、融合结果、耗时与调用计数）。
- 新增前端页面"改写实验室"（`/rewrite-lab`，归入"知识资产"），左侧为运行配置（Query、历史、路由模式、策略 Allowlist、预算、质量阈值），右侧按 HTML 原型的七个区块渲染真实运行结果。
- 明确不做：`/retrieval/search` 与 `/retrieval/ask` 的行为**不变**，问答链路接入改写是后续独立变更；不做 Rerank 模型（融合阶段只做去重 + RRF）；不做运行历史持久化。

## Capabilities

### New Capabilities

- `rag-query-rewrite`: 查询改写内核——查询理解、策略路由（Allowlist / 预算 / 依赖与互斥）、DAG 执行、双路检索与质量评估、条件二阶段补充、去重与 RRF 融合，以及 `RewriteTrace` 的输出契约与降级语义。
- `rag-rewrite-workbench`: 改写实验室这个可观测调试面——运行配置的提交与校验、Trace 的分步呈现、决策理由与失败/降级的可视化，以及运行成本（LLM 调用数、检索次数、耗时）的展示。

### Modified Capabilities

无。`knowledge-retrieval` 目前只存在于尚未归档的 `add-rag-retrieval` 变更中，`openspec/specs/` 下并无该能力；本次不改变 `/search`、`/ask` 的既有行为，因此不产生 delta。

## Impact

- **后端**：新增 `app/services/query_rewrite/`（理解、路由、策略执行器、评估、融合）；`app/api/v1/retrieval.py` 增一条只读运行路由；`app/schemas/retrieval.py` 增请求与 Trace 模型；`app/infra/llm/prompts.py` 增理解/改写/拆解/扩展四组提示词；`app/core/config.py` 增改写相关开关与预算默认值。
- **前端**：新增 `views/RewriteLabView.vue`、`api/query-rewrite.ts`；`api/types.ts` 增 Trace 类型；`router/index.ts` 增 `/rewrite-lab` 路由（权限沿用 `document:read`）。
- **复用而非新建**：`infra.llm.provider.chat_json` / `embed_texts` / `is_llm_configured`、`EntityRepository.list_entities`、现有 `document_chunk` 向量检索与密级过滤逻辑。
- **成本**：单次运行最坏 4 次 LLM 调用（理解、改写串行、拆解/扩展各一次）+ 8 次检索，典型 2–5 秒；HyDE 未启用即为规避长生成成本与幻觉风险。
- **风险**：LLM 改写引入未经证实的事实（以"不新增事实、只重组表达"的提示词约束 + 改写前后对照可见来控）；实验台暴露内部提示词与策略细节，仅对已登录且有 `document:read` 的用户开放。
