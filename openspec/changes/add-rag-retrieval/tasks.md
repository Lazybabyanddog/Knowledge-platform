# Tasks

## 1. 基础设施与配置

- [x] 1.1 把 `docker-compose.yml` 的 postgres 镜像改为 `pgvector/pgvector:pg16`，重建容器后执行 `CREATE EXTENSION IF NOT EXISTS vector`，验证 `select extversion from pg_extension where extname='vector'` 有输出且既有文档数据仍可查询
- [x] 1.2 在 `app/core/config.py` 增加 `embedding_base_url` / `embedding_api_key` / `embedding_model` / `embedding_dim=1024` / `embedding_batch_size=10` / `embedding_timeout_seconds` 与 `retrieval_top_k` / `retrieval_oversample` / `retrieval_max_sensitivity`，并在 `.env.example` 同步注释，验证 `Settings()` 在缺少这些变量时仍能启动
- [x] 1.3 新增 alembic 迁移：`document_chunk` 表（含 `vector(1024)` 列、唯一约束、HNSW 余弦索引）与 `document_version` 的 `index_status` / `index_error` / `indexed_at` / `chunk_count` 列，验证 `alembic upgrade head` 成功且 `alembic downgrade -1` 能干净回退
- [x] 1.4 新建 `app/infra/db/models/retrieval.py` 定义 `DocumentChunk` 模型并登记到模型汇总模块，验证 `Base.metadata.tables` 中存在 `document_chunk` 且字段与迁移一致

## 2. 切分与索引

- [x] 2.1 实现 `app/services/chunking.py`：按标题栈与块类型合并、表格独立成片段、超长块三级回退切分，读取策略 `config.chunk_hint`（缺省 800），验证单测覆盖「标题前缀」「表格独立」「丢弃块排除」「超长块不截断半句」四种情形
- [x] 2.2 在 `infra/llm/provider.py` 增加 `embed_texts()` 与 `is_embedding_configured()`，显式传 `dimensions`，返回长度不符时抛 `EmbeddingDimensionError`，验证单测用 mock 覆盖「正常批量」「维度不符」「单批失败重试后抛错」三种路径
- [x] 2.3 实现 `app/services/index_service.py`：同一事务内写入片段与向量、先按版本清空再重建、按块数分批调用 embedding、把结果写回 `document_version.index_status/chunk_count`，验证对同一版本连续执行两次后 `document_chunk` 行数不变
- [x] 2.4 在 `app/workers/tasks.py` 增加 `index.document_version` 与 `index.purge` 两个任务并在 `celery_app.py` 声明路由，未配置 embedding 时把版本标记为 `skipped` 并记录原因，验证手工调用任务后版本状态与片段数符合预期
- [x] 2.5 在 `ReviewService._publish()` 末尾投递建索引任务，在文档下架、软删除、恢复与重新触发解析的入口投递清理任务，验证批准一篇文档后其版本状态由 `pending` 变为 `ready` 且片段数大于 0
- [x] 2.6 实现 `app/scripts/reindex.py`（`--all` / `--document-id`）枚举 `published` 文档的当前版本并批量投递，验证在现有 1 篇已发布文档上运行时输出投递数与实际文档数一致
- [x] 2.7 在文档详情接口返回 `index_status` / `chunk_count` / `indexed_at` 并在 `schemas` 中补字段，验证 `/api/v1/documents/{id}` 响应包含这三个字段

## 3. 检索接口

- [x] 3.1 实现 `app/services/retrieval_service.py` 的 `search()`：单条 SQL 完成向量排序与 `workspace`/`status=published`/`deleted_at`/`is_current`/密级过滤，按 `retrieval_oversample` 过采样后截断到 k，验证构造一条 draft 与一条 published 文档时只返回后者
- [x] 3.2 实现密级可见规则（管理员四级、其他角色 `public`+`internal`）并在检索中生效，验证用管理员与普通账号查同一篇 `confidential` 文档时结果不同
- [x] 3.3 新增 `app/api/v1/retrieval.py` 的 `POST /retrieval/search`（权限 `document:read`），返回文档标题、版本号、片段内容、得分、页号范围与片段 ID，验证该路由挂到 `router.py` 后 `/docs` 中可见且 200 返回结构正确
- [x] 3.4 未配置 embedding 时 `search` 返回 `503` 与明确错误码，验证清空 `EMBEDDING_API_KEY` 后调用得到 503 而非空列表
- [x] 3.5 为 `search()` 补集成测试：覆盖跨 workspace 隔离、已下架文档不召回、同文档多版本只命中 `is_current` 版本三种情况，验证测试全部通过

## 4. 流式问答

- [x] 4.1 在 `infra/llm/provider.py` 增加异步流式 `astream_chat()`（`httpx.AsyncClient.stream`，逐行解析 `data:`，忽略 `[DONE]`），验证用 mock 流返回两段增量时能按序产出
- [x] 4.2 实现 `POST /retrieval/ask`：请求体含问题与可选 top_k，进入流之前完成检索并关闭 Session，按 `sources -> delta* -> done` 顺序输出 SSE，验证事件顺序为 `sources`、`delta` 若干、`done`，且 `sources` 先于第一个 `delta`
- [x] 4.3 检索零命中时不调用模型，直接输出空 `sources` 与固定说明事件，验证库里不存在的问题不会触发上游请求
- [x] 4.4 客户端断开时终止上游请求（`request.is_disconnected()` + 关闭生成器），验证断开后不再产出 `delta`/`done` 且上游生成器被关闭
- [x] 4.5 未配置 LLM 时在流开始前返回 `503`，模型中途报错时推送 `error` 事件并结束，验证两种情况分别得到 503 与 `error` 帧
- [x] 4.6 生成提示词只允许引用 `sources` 内容并要求片段不足时明确说明，验证提示词含编号资料与「只能依据」约束，零命中时输出固定「未找到」说明
- [x] 4.7 问答写入审计事件（调用者、问题摘要前 100 字、命中片段数），验证一次问答后 `audit_log` 新增对应记录且不含完整答案

## 5. 前端问答页

- [x] 5.1 新增 `src/api/retrieval.ts`（`search` 走现有 http 封装，`ask` 用 `fetch` + `AbortController` 并手动携带令牌），验证类型定义与后端响应字段一致
- [x] 5.2 实现 SSE 解析工具（`ReadableStream` + `TextDecoder` 按 `\n\n` 分帧、按行拆 `event:`/`data:`），验证单测覆盖「一帧跨多个 chunk 到达」与「一帧含多个事件」
- [x] 5.3 新增 `src/views/AskView.vue`：问题输入、答案流式渲染、引用卡片（标题 + 页码 + 片段）、停止生成按钮，验证真实提问时答案逐字出现且引用可展开
- [x] 5.4 在 `router/index.ts` 注册 `/ask`（`meta.group` 归入知识资产、权限 `document:read`），验证左侧菜单出现「知识问答」且未登录跳转登录
- [x] 5.5 空结果、503、网络中断三种状态给出可读提示而非空白页，验证分别制造这三种情况时页面表现符合预期

## 6. HTML 审批与集成验收

- [x] 6.1 在 `app/schemas/review.py` 的 `DocTypeLiteral` 增加 `"html"`，验证创建 `doc_type=html` 的审批流返回 201 且 `doc_type=markdown` 仍被拒绝
- [x] 6.2 用一篇 HTML 文档走完「配置默认流 → 提审 → 批准 → 索引 → 问答」全链路，验证问答答案能引用到该文档的页码
- [x] 6.3 更新 README：pgvector 镜像要求、`EMBEDDING_*` 配置、HTML 需先配置默认审批流、索引重建命令、批量重建需另起 worker 的说明，验证按文档步骤在空库上可复现
- [x] 6.4 回归验证未配置 embedding 时解析、抽取、图谱、审批、文档列表全部行为不变

## Workflow follow-up

- 满足评审要求后运行 `openspec archive add-rag-retrieval`，把 `knowledge-retrieval` 与 `document-review` 同步到主规格。
- 归档后确认 `openspec/specs/knowledge-retrieval/spec.md` 与 `openspec/specs/document-review/spec.md` 已生成。
