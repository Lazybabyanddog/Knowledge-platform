# Proposal

## Why

知识库已经能把文档解析成结构化块、抽取成图谱，但没有任何检索能力：解析产物只能按 `parse_block.seq` 顺序浏览，沉淀下来的内容无法被提问。业务侧现在需要的是"问一个问题，得到基于已发布文档的回答，并能点回原文"。

现在做的条件是齐的：解析产物带 `page_no` / `bbox` 锚点、审批流能保证"只有被批准的版本才算生效"、文档有 `workspace` + `sensitivity` 分级。缺的只是切分、向量化与生成这三层。

## What Changes

- 新增 **chunk 层**：把 `parse_block` 按 `chunk_hint`（默认 800 字）合并成可检索片段，保留 `block_ids` 反向引用与标题路径；chunk 持久化在 `document_chunk` 表。
- 新增 **embedding 适配**：`infra/llm` 增加 OpenAI 兼容的 `/embeddings` 客户端，模型 `qwen3.7-text-embedding`，显式传 `dimensions=1024`，与 chat 配置分离（`EMBEDDING_*`）。
- **PostgreSQL 换 pgvector 镜像**：`postgres:16-alpine` → `pgvector/pgvector:pg16`，启用 `vector` 扩展，`document_chunk.embedding vector(1024)` + HNSW 余弦索引。
- 新增 **索引生命周期**：审批通过（`review_service._publish`）后异步构建该版本索引；文档下架 / 删除 / 重新解析产生新版本时清理旧索引。索引只覆盖 `status=published` 的 `current_version`。
- 新增 **检索与问答 API**：`POST /api/v1/retrieval/search`（片段召回）与 `POST /api/v1/retrieval/ask`（SSE 流式答案 + 引用），均按 `workspace_id`、`status=published`、`sensitivity` 过滤。
- 新增 **问答页面**：左侧导航"知识资产 / 知识问答"，流式增量渲染，答案下方给出引用片段（文档标题、页码、原文节选）。
- **审批流类型扩到 HTML**：`ReviewFlowCreateRequest.doc_type` 放开 `html`，使 HTML 文档可提交审批并发布，从而进入检索范围。前端类型枚举已含 `html`，本次只补后端契约。
- 契约变更：`document.status=archived` 与 `document` 软删除都会触发索引清理；`/retrieval/ask` 在未配置 embedding 或 LLM 时返回明确错误码，不返回空答案。

## Capabilities

### New Capabilities

- `knowledge-retrieval`: 从解析产物构建可检索片段、维护其与文档版本的索引生命周期、提供向量召回与基于引用的流式问答。
- `document-review`: 审批流配置的文档类型覆盖范围（本次扩展至 HTML），保证所有可解析类型都能进入发布通道。

### Modified Capabilities

（无：`openspec/specs/` 当前为空，本次以新增能力形式沉淀。）

## Impact

- **数据库**：新增 `document_chunk` 表与 HNSW 索引；`pgvector` 扩展；`docker-compose.yml` 中 postgres 镜像变更（同 PG16 主版本，数据卷兼容）。
- **后端**：`app/core/config.py`（`embedding_*`、`retrieval_*`）、`app/infra/llm/provider.py`（`embed`）、新增 `app/infra/vector/`、`app/services/retrieval_service.py`、`app/services/index_service.py`、`app/api/v1/retrieval.py`、`app/workers/tasks.py`（索引任务）、`app/services/review_service.py`（发布/下架挂钩）、`app/schemas/review.py`（`DocTypeLiteral`）、`alembic/versions/` 新迁移。
- **前端**：新增 `src/api/retrieval.ts`、`src/views/AskView.vue`、路由与菜单；SSE 需用 `fetch` + `ReadableStream`（axios 不支持流式）。
- **运维**：`.env.example` 增加 `EMBEDDING_*`；HTML 文档发布前需管理员配置 `doc_type=html` 的默认审批流；提供按 `published` 全量重建索引的脚本。
- **不影响**：解析、抽取、图谱、审批的既有行为；未配置 embedding 时除问答外全部功能照旧可用。
