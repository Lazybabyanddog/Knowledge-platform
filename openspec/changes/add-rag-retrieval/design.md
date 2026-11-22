# Design

## Context

参见 `proposal.md` 的 Why。实现前需要认清的现状约束（均已核实）：

- `parse_block` 是解析产物的唯一落点：`(parse_task_id, seq)` 唯一，字段含 `block_type`、`content`、`page_no`、`bbox`、`level`、`is_discarded`、`metadata`。粒度不均匀：标题一行、正文一段、表格整块。
- 解析策略里已有 `config.chunk_hint`（seed 默认 800），但代码中无任何消费点——切分层是预留未建。
- 文档状态机：`draft -> pending -> published -> archived -> draft`，`published` 不能直接回 `draft`。发布动作集中在 `ReviewService._publish()`（`app/services/review_service.py:666`），会同时把旧版本 `is_current=False`。
- PostgreSQL 16.16 运行在 `postgres:16-alpine`，`pg_available_extensions` 中无 `vector`；`pg_trgm` 也未安装。
- `infra/llm/provider.py` 只有同步 `chat_json()`，未配置时抛 `LLMError`；`config.py` 只有 `llm_*` 一组配置。
- 后端为 FastAPI + 同步 SQLAlchemy Session，Celery 单队列 `parse`、`--pool=solo` 常驻。
- 前端 axios（`src/api/http.ts`），无 SSE 基础设施；路由按 `meta.group` 分组渲染菜单。
- `openspec/specs/` 为空，本次以新能力形式沉淀。

## Goals / Non-Goals

**Goals:**

- 一条可重复执行的链路：`parse_block -> chunk -> embedding -> pgvector -> 召回 -> 生成`，每步都能单独重试。
- 引用可落到"文档 + 版本 + 页号 + 块区间"，与既有 `bbox` 锚点兼容（本期前端只展示到页号）。
- 未配置 embedding / LLM 时系统行为明确：要么返回可诊断的错误，要么跳过索引，不产生"看起来正常但答不出"的假象。

**Non-Goals:**

- 不做 rerank、不做 query 改写、不做多轮会话记忆、不做混合检索（BM25 + 向量）。
- 不做图增强答案（GraphRAG）；图谱与片段暂不联合召回。
- 不做文档级 ACL 的新逻辑；沿用"workspace + 发布态 + 密级"这一现有口径。
- 不做 PDF OCR 的坐标高亮前端渲染。

## Decisions

### D1. 片段持久化在 `document_chunk` 表，而不是查询时拼接块

引用需要稳定标识，重建需要可差分，检索要能直接 `ORDER BY embedding`。视图或临时拼接三者都做不到。

```
document_chunk
  id                 uuid pk
  workspace_id       uuid  -> workspace(id)                 -- 冗余，用于过滤免 join
  document_id        uuid  -> document(id) on delete cascade
  document_version_id uuid -> document_version(id) on delete cascade
  parse_task_id      uuid  -> parse_task(id) on delete cascade
  seq                int                                    -- 版本内顺序
  content            text                                   -- 含标题前缀的片段正文
  heading_path       text                                   -- "第三章 预算 / 3.2 支出"
  block_ids          jsonb                                  -- 覆盖的块 ID 列表，保序
  first_page_no      int
  last_page_no       int
  char_count         int
  embedding          vector(1024)
  embedding_model    varchar(64)
  created_at         timestamptz

  unique (parse_task_id, seq)
  index  (document_version_id, seq)
  index  (workspace_id, document_id)
  index  hnsw (embedding vector_cosine_ops) with (m=16, ef_construction=64)
```

备选：直接给 `parse_block` 加 `embedding` 列。否决理由——块粒度不均（实测一篇公报 56 块里既有 1 行标题也有整块表格），召回的片段会忽长忽短；且同一块可能被多个问答场景复用不同窗口。

### D2. 切分算法：按块类型分组的线性合并

输入是一个 `parse_task_id` 的全部块（`seq` 升序，跳过 `is_discarded`），输出片段列表：

```
标题栈 heading_path          <-- block_type in (title, heading) 时压栈/替换
current = []                 <-- 当前片段累积
for block in blocks:
    if block.block_type == table:     冲刷 current，表格单独成片段
    elif block.block_type in (title, heading): 冲刷 current，更新 heading_path
    else:
        if len(current) + len(block) > chunk_hint: 冲刷 current
        append block
```

- 冲刷时生成片段：`content = heading_path + "\n" + 段落拼接`，记录首尾 `page_no` 与 `block_ids`。
- 单块超过 `chunk_hint` 时按 `\n\n` → `。` → 硬切 三级回退，保证不截断到半个句子。
- `chunk_hint` 取自该文档版本所用解析策略的 `config.chunk_hint`，缺省 800。

备选：固定字符窗口滑动 + 重叠。否决理由——会切断表格与标题上下文，且重叠导致同一段内容被检索命中两次，答案里出现重复引用。

### D3. 向量存储选 pgvector，索引参数按当前规模设定

权限过滤与向量排序在同一条 SQL 完成：

```sql
SELECT c.id, c.content, d.title, ...
FROM document_chunk c
JOIN document d ON d.id = c.document_id
JOIN document_version v ON v.id = c.document_version_id
WHERE d.workspace_id = :ws
  AND d.status = 'published' AND d.deleted_at IS NULL
  AND d.sensitivity = ANY(:allowed)
  AND v.is_current
ORDER BY c.embedding <=> :qvec
LIMIT :k
```

备选对比：

| 方案 | 结论 |
|---|---|
| Neo4j 向量索引 | 否决：无 SQL 级联过滤，只能先召回再过滤，`published`/密级条件下召回率会掉；且 5 万字符规模用不上图索引优势 |
| Milvus / Qdrant | 否决：为当前语料引入第三套存储与运维成本，收益为零 |
| pgvector | 采用：单库事务、过滤与排序同源、`create extension` 即得 |

过滤条件下 HNSW 可能召回不足：查询时先 `SET LOCAL hnsw.ef_search = 100`，并取 `LIMIT k * 4` 后在应用层截断到 k；不使用 `iterative_scan`（依赖 pgvector ≥ 0.8，镜像版本不作为前提）。

### D4. 索引构建挂在发布动作上，异步执行

`ReviewService._publish()` 末尾投递 `index.document_version` 任务（参数 `document_version_id`）。同一条链路上：

- 下架（`published -> archived`）、软删除、恢复、重新触发解析 → 投递 `index.purge` 任务按 `document_version_id` 清理片段。
- 任务幂等：先 `DELETE FROM document_chunk WHERE document_version_id = :v`，再重建；重复投递结果一致。
- 复用现有 `parse` 队列（worker 已常驻，避免再起进程），代价是批量重建会占用解析 worker——在 README 里注明"大规模重建时单独起一个 `-Q parse` worker"。

版本上的索引状态用列记录，避免再建状态表：

```
document_version
  + index_status  varchar(16)  default 'pending'   -- pending/building/ready/failed/skipped
  + index_error   text
  + indexed_at    timestamptz
  + chunk_count   int default 0
```

备选：解析时建索引 + 查询时过滤。否决理由——给 `draft` 内容建索引既浪费算力，也让密级更高但未发布的内容进入同一张表，隔离性变差。

### D5. embedding 客户端独立于 chat 配置

```
EMBEDDING_BASE_URL=            # 兼容模式，如 https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=qwen3.7-text-embedding
EMBEDDING_DIM=1024
EMBEDDING_BATCH_SIZE=10        # 供应商单请求条数上限
EMBEDDING_TIMEOUT_SECONDS=60
```

`infra/llm/provider.py` 新增 `embed_texts(texts: list[str]) -> list[list[float]]`：

- 请求体显式带 `dimensions=settings.embedding_dim`，返回向量长度不等于配置值时抛 `EmbeddingDimensionError`（不截断、不补零）。
- 单批 ≤ `EMBEDDING_BATCH_SIZE`，失败按 1s/2s/4s 重试 3 次；仍失败则整个版本构建失败。
- `is_embedding_configured()` 与 `is_llm_configured()` 同构，判定 `api_key` 与 `base_url` 均非空。

复用 `llm_*` 的备选被否决：chat 与 embedding 常来自不同供应商或不同额度池，混用会让换模型牵连另一侧。

### D6. 问答用异步生成器直出 SSE，检索在进入流之前完成

```
POST /api/v1/retrieval/ask
  -> async def
  -> await run_in_threadpool(retrieve, ...)   # 同步 Session，取出片段后立即关闭
  -> StreamingResponse(event_stream(), media_type="text/event-stream")
```

事件协议（每帧 `event:` + 单行 `data:` JSON）：

```
event: sources   data: {"sources":[{"index":1,"chunk_id":"...","document_id":"...","title":"...","page_no":12,"snippet":"..."}]}
event: delta     data: {"text":"根据"}
event: delta     data: {"text":"《XX 办法》"}
event: done      data: {"elapsed_ms":2310,"sources":3}
event: error     data: {"code":"LLM_CALL_FAILED","message":"..."}
```

- 先发 `sources` 再发 `delta`：前端可以先渲染引用卡片，答案逐字补齐，避免"答案出来后才看到出处"。
- 生成走 `httpx.AsyncClient.stream("POST", ...)`，`async for chunk` 解析 `data:` 行后转发；`request.is_disconnected()` 为真时 `break` 并关闭上游连接。
- 检索零命中时不调用模型：直接 `sources: []` + 一段固定说明 + `done`。
- 未配置 LLM/embedding → 返回 503（在流开始前判断，保证状态码正确）。

备选：同步生成器 + 同步 `httpx.stream`。否决理由——会占满线程池（默认 40）线程直到流结束，多用户并发时新请求直接排队。

### D7. 权限与可见性口径沿用现状，不新增 ACL 逻辑

- 权限点复用 `document:read`，不新增 `retrieval:*`，避免改动角色映射表。
- 密级过滤规则：`admin` 可见全部四级；其他角色可见 `public` 与 `internal`。这是一条明确的新规则，写在 spec 的过滤需求里，而非隐式实现。
- `document_acl` 表当前未被列表查询使用，本期检索保持一致（同样不过滤），避免检索与列表口径不一致造成"列表看不到、问答能问到"的诡异现象。

### D8. HTML 审批纳入：只改后端契约

`app/schemas/review.py:12` 的 `DocTypeLiteral` 增加 `"html"`。前端 `DOC_TYPE` 已含 `html`、`ReviewFlowsView` 选项由其动态生成，无需改动；`ReviewService._resolve_flow()` 已具备"未配置默认流"的报错分支。不追加种子审批流，保持"管理员显式配置"的现状。

### D9. 索引重建脚本与管理端状态

- `python -m app.scripts.reindex [--document-id X] [--all]`：按 `published` 枚举当前版本，逐个投递 `index.document_version`，输出投递数量。
- 文档详情接口返回 `index_status`、`chunk_count`、`indexed_at`；前端详情页展示一行索引状态，不新增页面。

## Risks / Trade-offs

- **换镜像导致数据库不可用** → 同为 PG16 主版本，数据目录兼容；迁移前 `pg_dump` 一份兜底，`docker compose up -d postgres` 后立即 `CREATE EXTENSION IF NOT EXISTS vector`。
- **过滤 + HNSW 召回不足，出现"明明有内容却答不出"** → 过采样 4 倍 + `ef_search=100`；规模增长后改为 `iterative_scan` 或先按 `workspace` 分区索引。
- **索引任务占用解析 worker** → 任务幂等且可重试；批量重建时另起 worker；README 写明。
- **嵌入式模型限流/超时** → 批大小 10、三次退避重试、单批失败即整版失败并落 `index_error`，不写入半成品。
- **流式长连接在单进程 uvicorn 下堆积** → 问答并发低，先接受；`done` 后主动关闭生成器，前端 `AbortController` 取消。
- **密级规则改变可见范围** → 非 admin 只能检索 `public`/`internal`，若现有语料多为 `confidential`，演示时会看起来"什么都搜不到"；上线前需确认语料密级分布。
- **中文不分词** → 向量检索不依赖分词，`pg_trgm` 不安装，避免引入额外扩展。

## Migration Plan

1. `pg_dump` 备份 → 修改 `docker-compose.yml` 的 postgres 镜像为 `pgvector/pgvector:pg16` → `docker compose up -d postgres`。
2. `CREATE EXTENSION IF NOT EXISTS vector;`（`ai_platform` 即超级用户）。
3. 新增 alembic 迁移：`document_chunk` 表 + HNSW 索引 + `document_version` 四个索引状态列。
4. `.env` 增加 `EMBEDDING_*` 五项，重启 uvicorn 与 celery。
5. 管理员配置一条 `doc_type=html` 的默认审批流，走一遍"提审 → 批准 → 索引 → 问答"。
6. 回滚：任务层可停用（问答路由返回 503 由配置控制前先下线路由）；数据层 `DROP TABLE document_chunk` 并删除新增列；镜像回退 `postgres:16-alpine`（PG16 数据目录兼容，`vector` 扩展残留不影响启动）。

## Open Questions

- 是否需要在片段表中额外冗余 `doc_type`，用于前端按类型过滤？当前可用 join 取得，暂不冗余，索引规模上来后再评估。
