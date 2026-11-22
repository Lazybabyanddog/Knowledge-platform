# Design

## Context

项目当前为空仓库（仅已初始化 `openspec/`），无既有代码与数据，不存在迁移包袱，但也意味着所有边界必须一次定义清楚。

影响设计的事实（动机见 proposal.md）：

- 技术栈已定：后端 Python + FastAPI + LangChain + LangGraph；前端 Vue3；PostgreSQL + Neo4j；MinIO；Celery + Redis
- 本期不做检索问答，但解析产物必须保留可定位锚点（页码 + 坐标），否则下一期要全量重跑
- 单租户部署，所有业务表预留 `workspace_id`，为后续多空间留路
- 反馈治理不在本期，但文档/解析/图谱的状态字段要预留未来巡检规则的可判定性

## Goals / Non-Goals

**Goals:**

- 定义稳定的分层与目录结构，使后续新增"检索""反馈治理"时不需要重构
- 明确 PostgreSQL 与 Neo4j 的单一真相边界，避免双写
- 明确 LangGraph 的适用边界：只用于 AI 加工流水线，不用于业务审批流
- 给出字段级表结构与接口契约，使开发可直接按文档落码

**Non-Goals:**

- 不设计检索索引、向量库与嵌入模型（下一期）
- 不设计反馈治理的任务模型与巡检规则引擎（下一期）
- 不设计 SSO、SCIM、多租户隔离（保留字段，不实现）
- 不设计高可用、水平扩展与容灾
- 不设计前端视觉规范（只定目录与路由边界）

## 架构与分层

```
+---------------------------------------------------------------+
|  Vue3 SPA  (Element Plus + Pinia + Vue Router)                  |
+-------------------------------+-------------------------------+
                                | HTTP / REST (JSON)
+-------------------------------v-------------------------------+
|  API 层  FastAPI Routers                                       |
|  只做: 参数校验 / 鉴权依赖 / 调用 service / 组装响应              |
+-------------------------------+-------------------------------+
|  应用层  Services (用例编排)                                    |
|  事务边界在这里: 一个用例一个事务                                 |
+-------------------------------+-------------------------------+
|  领域层  Domain                                                |
|  Enums / StateMachine / Policies / 不依赖框架                   |
+-------+----------------+----------------+---------------------+
        |                |                |
+-------v------+  +------v-------+  +-----v--------+
| 关系库仓储     |  | 图库仓储      |  | 对象存储      |
| SQLAlchemy   |  | Neo4j Driver |  | MinIO SDK    |
| PostgreSQL   |  |              |  |              |
+--------------+  +--------------+  +--------------+
                        ^
                        | 写入
+-----------------------+---------------------------------------+
|  Celery Worker                                                |
|  parse_task  -> LangGraph parse graph   -> 写 parse_block      |
|  extract_task -> LangGraph extract graph -> 写候选实体关系      |
+---------------------------------------------------------------+
```

分层铁律：

- Router 不写业务判断；Service 不直接写 SQL；Domain 不 import FastAPI/SQLAlchemy
- 状态迁移只经 `domain/state_machines.py` 校验，禁止在 service 里散落 if 判断
- 跨存储（PG 与 Neo4j）不做分布式事务，一律"先落业务状态，再写图，失败可重放"

## 项目目录结构

```
Knowledge-platform/
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/versions/             迁移脚本
│   ├── app/
│   │   ├── main.py                   FastAPI 实例、中间件、异常处理器
│   │   ├── core/
│   │   │   ├── config.py             Settings（pydantic-settings）
│   │   │   ├── security.py           密码哈希、JWT 签发解析
│   │   │   ├── exceptions.py         业务异常 + 全局处理器
│   │   │   ├── response.py           统一响应包装
│   │   │   ├── pagination.py
│   │   │   ├── logging.py            request_id 注入
│   │   │   └── deps.py               get_db / get_current_user / require_roles
│   │   ├── api/v1/                   auth users documents parse_strategies
│   │   │                             parse_tasks ontology extraction
│   │   │                             entities graph reviews router
│   │   ├── schemas/                  Pydantic 请求/响应模型
│   │   ├── services/                 auth user document storage parse
│   │   │                             ontology extraction graph review audit
│   │   ├── domain/
│   │   │   ├── enums.py              状态枚举，与库中取值一一对应
│   │   │   ├── state_machines.py     迁移表与校验函数
│   │   │   ├── policies.py           角色与对象级权限判定
│   │   │   └── models.py             领域对象（与 ORM 解耦）
│   │   ├── infra/
│   │   │   ├── db/
│   │   │   │   ├── session.py        引擎、会话、事务上下文
│   │   │   │   ├── base.py           公共字段
│   │   │   │   ├── models/           每表一个 ORM 文件
│   │   │   │   └── repositories/     每表一个仓储，收敛查询
│   │   │   ├── graph/
│   │   │   │   ├── driver.py
│   │   │   │   ├── schema.cypher     约束与索引初始化
│   │   │   │   └── repositories/     entity / relation / class
│   │   │   ├── storage/minio_client.py
│   │   │   ├── cache/redis_client.py
│   │   │   └── llm/
│   │   │       ├── provider.py       模型工厂，可切换供应商
│   │   │       └── prompts/          抽取提示词，按任务分子目录
│   │   ├── pipelines/                LangGraph
│   │   │   ├── state.py              TypedDict 状态定义
│   │   │   ├── parse/{graph,nodes,routers}.py
│   │   │   └── extract/{graph,nodes,validators}.py
│   │   └── workers/
│   │       ├── celery_app.py
│   │       └── tasks/{parse,extract}.py
│   └── tests/{unit,integration,fixtures}
├── frontend/
│   ├── vite.config.ts / package.json / tsconfig.json
│   └── src/
│       ├── main.ts / App.vue
│       ├── api/                      http.ts 与各模块，统一拦截与刷新令牌
│       ├── router/index.ts           路由与登录守卫
│       ├── stores/                   auth.ts / app.ts
│       ├── views/
│       │   ├── login/LoginView.vue
│       │   ├── document/{DocumentListView,DocumentDetailView}.vue
│       │   ├── parse/{StrategyListView,ParseTaskListView}.vue
│       │   ├── graph/{OntologyView,EntityReviewView,GraphExploreView}.vue
│       │   ├── review/{ReviewTaskView,ReviewFlowView}.vue
│       │   └── system/UserListView.vue
│       ├── components/               上传器、块预览、状态徽标
│       ├── types/                    与后端 schema 对应的 TS 类型
│       └── utils/
└── deploy/
    ├── docker-compose.yml            postgres / neo4j / minio / redis / api / worker / web
    ├── Dockerfile.backend
    ├── Dockerfile.frontend
    └── .env.example
```

## 数据模型（PostgreSQL）

约定：主键统一 `uuid`，时间统一 `timestamptz`，状态用 `varchar(16)` 且与 `domain/enums.py` 一一对应，所有表带 `created_at`。

### 身份与空间

```sql
workspace(id, name, slug UNIQUE, description, status, created_at, updated_at)

app_user(
  id, workspace_id, username UNIQUE, email, password_hash,
  display_name, status, last_login_at, created_at, updated_at
)

role(id, code UNIQUE, name, description, is_system)
user_role(user_id, role_id)                      -- 联合主键

refresh_token(
  id, user_id, token_hash UNIQUE, expires_at, revoked_at,
  user_agent, ip, created_at
)                                                -- INDEX(user_id)
```

### 文档

```sql
document(
  id, workspace_id, title, summary,
  source_type,          -- upload / url / import
  doc_type,             -- pdf / docx / pptx / xlsx / txt / md / image ...
  status,               -- draft / pending / published / rejected / archived
  current_version_id, owner_id, sensitivity,
  tags JSONB,           -- ["差旅","财务"]
  extra_metadata JSONB,
  deleted_at, created_by, created_at, updated_at
)
-- INDEX(workspace_id, status, deleted_at, created_at DESC)
-- GIN INDEX(tags)

document_version(
  id, document_id, version_no, storage_key, file_name, file_size,
  mime_type, checksum, page_count, is_current, change_note,
  created_by, created_at
)
-- UNIQUE(document_id, version_no)
-- UNIQUE(document_id, checksum)     同一文档内容重复校验

document_acl(id, document_id, subject_type, subject_id, permission)
-- 本期建表不启用，预留对象级权限
```

### 解析

```sql
parse_strategy(
  id, workspace_id, strategy_key, version, name, doc_type, engine,
  config JSONB,       -- {ocr, remove_header, table_mode, chunk_hint, ...}
  is_default, status, created_by, created_at
)
-- UNIQUE(strategy_key, version)      修改即新增版本，历史任务引用旧版本
-- 部分唯一索引: UNIQUE(workspace_id, doc_type) WHERE is_default

parse_task(
  id, document_id, document_version_id, strategy_id,
  status,             -- pending / running / succeeded / failed / cancelled
  attempt, quality_score, stats JSONB,
  error_code, error_message, started_at, finished_at, created_at
)
-- INDEX(document_version_id, status)

parse_task_attempt(
  id, parse_task_id, attempt_no, status, quality_score,
  stats JSONB, error_code, error_message, started_at, finished_at
)                                                -- 每次重试一条，保留失败历史

parse_block(
  id, parse_task_id, document_version_id, seq, block_type,
  content, page_no, bbox JSONB, level, parent_block_id,
  is_discarded, metadata JSONB
)
-- UNIQUE(parse_task_id, seq)         保证原文顺序稳定
-- INDEX(document_version_id, seq)
```

`bbox` 形如 `{"x0":120,"y0":340,"x1":560,"y1":372}`，坐标系为该页原始像素，供下一期检索高亮使用。

### 本体与抽取

```sql
ontology_class(
  id, workspace_id, code, name, description, parent_id,
  attributes JSONB, version, status
)                                                -- UNIQUE(workspace_id, code)

ontology_relation(
  id, workspace_id, code, name, description,
  source_class_id, target_class_id, version, status
)                                                -- UNIQUE(workspace_id, code)

extraction_task(
  id, document_id, document_version_id, parse_task_id,
  ontology_version, config JSONB, status, attempt, stats JSONB,
  error_code, error_message, started_at, finished_at, created_at
)
```

### 审批

```sql
review_flow(id, workspace_id, name, doc_type, is_default, status, created_at)
-- steps 存 JSONB:
-- [{index:1, name:"内容审核", approver_type:"role", approver_ref:"reviewer", skippable:false}]

review_instance(
  id, document_id, document_version_id, flow_id, flow_snapshot JSONB,
  current_step, status,          -- pending / approved / rejected / withdrawn
  submitter_id, submitted_at, finished_at
)                                -- INDEX(status, current_step)

review_record(
  id, instance_id, step_index, reviewer_id,
  action,                        -- approve / reject / withdraw
  comment, created_at
)
```

`flow_snapshot` 在提交时冻结审批流定义，避免管理员事后改流程影响进行中的审批。

### 审计

```sql
audit_log(
  id BIGSERIAL, workspace_id, actor_id, actor_name,
  action, object_type, object_id, before JSONB, after JSONB,
  ip, request_id, created_at
)
-- INDEX(workspace_id, created_at DESC), INDEX(object_type, object_id)
-- 按月分区，保留 12 个月
```

## 数据模型（Neo4j）

**单一真相边界**：PostgreSQL 保存业务状态、本体定义与任务记录；Neo4j 保存实体、关系与提及。两库不双写同一份数据。

```
(:Class {code, name, workspace_id})
    -- 由 PostgreSQL 单向投影，仅用于图查询时的类型过滤

(:Entity {
   id, workspace_id, class_code, name, normalized_name,
   aliases: [string], description,
   status: 'proposed' | 'confirmed' | 'rejected' | 'merged',
   confidence: float,
   merged_into: <string>,
   extraction_task_id, document_id, created_at, updated_at
})
-- UNIQUE(Entity.id)
-- INDEX(Entity.normalized_name), INDEX(Entity.status), INDEX(Entity.workspace_id)

(:Entity)-[:INSTANCE_OF]->(:Class)

(:Entity)-[:REL {
   id, code, status, confidence,
   evidence_block_id, document_id, extraction_task_id, created_at
}]->(:Entity)
-- UNIQUE(REL.id)
```

取舍说明：合并实体、重挂关系在图库内是单事务操作；若实体也存在于关系库，就必须跨库保证一致性。把实体限定在图库后，这一整类问题消失。代价是实体列表的分页与复杂筛选要用 Cypher 实现，本期数据量级下可接受。

## LangGraph 的边界

本设计最关键的一条决策：

| 用途 | 使用 LangGraph | 理由 |
| --- | --- | --- |
| 文档解析流水线 | 是 | 按文档类型多分支、OCR 条件兜底、质量门禁，天然是图 |
| 知识抽取流水线 | 是 | 抽取 → 校验 → 修复 → 重试的循环，需断点续跑 |
| 文档审批流 | **否** | 审批是业务状态机，要求事务性、可配置、可审计；埋进图代码后改流程就要改代码发版 |

审批流改用「表驱动」：`review_flow.steps` 是配置，`review_instance.current_step` 是游标，每次动作推进一步。管理员改流程不需要发版。

### 解析图

```
[start] -> detect_type
   +-- pdf    -> layout_parse -> need_ocr? --yes--> ocr_parse --+
   |                             |no                           |
   +-- office -> office_parse ---------------------------------+
   +-- image  -> ocr_parse ------------------------------------+
   +-- text   -> plain_parse ----------------------------------+
                                                               v
                                                    table_extract -> normalize
                                                               v
                                                        quality_score
                                    +--------------------------+---------------------+
                                    |                                                |
                              score >= 60                                      score < 60
                                    |                                                |
                             persist_blocks                                  persist_blocks
                                    |                                                |
                                  [end]                            mark_low_quality -> [end]
```

状态：`{task_id, storage_key, doc_type, strategy, blocks[], warnings[], quality, error}`。图带 checkpoint，支持按 `task_id` 恢复。

### 抽取图

```
[start] -> load_blocks
   -> map (每批 N 个块并行)
        classify_block
        extract_entities
        extract_relations
        schema_validate --不合规--> repair_output --(最多2次)--> schema_validate
                                                                  |超限
                                                                  v
                                                           记录跳过并继续下一批
   -> reduce -> dedupe_candidates -> persist_candidates (status=proposed) -> [end]
```

约束：抽取节点必须输出结构化 JSON；`repair_output` 只做格式修复与本体约束校正，不重新生成内容；超过 2 次仍不合规的块被跳过并计入 `stats.skipped_blocks`，任务整体仍记为成功。

## 接口设计

### 通用约定

统一响应体：

```json
{ "code": 0, "message": "ok", "data": {}, "request_id": "..." }
```

分页参数 `page`（从 1 开始）、`size`（默认 20，上限 100）；分页结果 `{"items": [], "total": 0, "page": 1, "size": 20}`。

错误码：

| code | HTTP | 含义 |
| --- | --- | --- |
| 0 | 200 | 成功 |
| 40001 | 400 | 参数校验失败 |
| 40101 | 401 | 未认证 |
| 40102 | 401 | 令牌过期（客户端应触发刷新） |
| 40301 | 403 | 权限不足 |
| 40401 | 404 | 资源不存在 |
| 40901 | 409 | 状态冲突或重复 |
| 41301 | 413 | 文件过大 |
| 42901 | 429 | 请求过频 |
| 50000 | 500 | 服务内部错误 |

认证方式：`Authorization: Bearer <access_token>`，访问令牌有效期 2 小时，刷新令牌 7 天且一次性。

### 认证与用户

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | 公开 | 用户名密码登录 |
| POST | `/api/v1/auth/refresh` | 公开 | 刷新令牌换新令牌 |
| POST | `/api/v1/auth/logout` | 登录用户 | 使当前会话失效 |
| GET | `/api/v1/auth/me` | 登录用户 | 当前用户与角色 |
| GET | `/api/v1/users` | 管理员 | 用户列表 |
| POST | `/api/v1/users` | 管理员 | 创建用户 |
| PATCH | `/api/v1/users/{id}` | 管理员 | 改显示名/角色/状态 |
| POST | `/api/v1/users/{id}/reset-password` | 管理员 | 重置密码 |

`POST /api/v1/auth/login` 请求 `{username, password}`，响应 `{access_token, refresh_token, expires_in, user:{id, display_name, roles[]}}`。

### 文档

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/documents` | 登录用户 | 列表，支持 keyword/status/tag/sensitivity/created_by/时间范围 |
| POST | `/api/v1/documents` | 贡献者+ | `multipart/form-data`：file + title + tags + sensitivity |
| POST | `/api/v1/documents/batch` | 贡献者+ | 多文件，逐项返回成功/失败 |
| GET | `/api/v1/documents/{id}` | 登录用户 | 详情含当前版本与状态 |
| PATCH | `/api/v1/documents/{id}` | 贡献者+ | 改标题/摘要/标签/密级 |
| DELETE | `/api/v1/documents/{id}` | 贡献者+ | 软删除 |
| POST | `/api/v1/documents/{id}/restore` | 管理员 | 恢复 |
| GET | `/api/v1/documents/{id}/versions` | 登录用户 | 版本列表 |
| POST | `/api/v1/documents/{id}/versions` | 贡献者+ | 上传新版本 |
| GET | `/api/v1/documents/{id}/versions/{vid}/download` | 登录用户 | 返回 15 分钟内有效的预签名地址 |

### 解析

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/parse-strategies` | 登录用户 | 策略列表（默认返回每种类型的最新版本） |
| POST | `/api/v1/parse-strategies` | 管理员 | 新建策略 |
| PATCH | `/api/v1/parse-strategies/{id}` | 管理员 | 修改（生成新版本） |
| POST | `/api/v1/parse-tasks` | 贡献者+ | 触发解析 `{document_version_id, strategy_id?}` |
| GET | `/api/v1/parse-tasks` | 登录用户 | 任务列表 |
| GET | `/api/v1/parse-tasks/{id}` | 登录用户 | 详情含尝试历史与质量评分 |
| POST | `/api/v1/parse-tasks/{id}/retry` | 贡献者+ | 重试（上限 3 次） |
| POST | `/api/v1/parse-tasks/{id}/cancel` | 贡献者+ | 取消 |
| GET | `/api/v1/parse-tasks/{id}/blocks` | 登录用户 | 产物分页，可用 `include_discarded` 控制是否含页眉页脚 |

### 本体与图谱

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET/POST | `/api/v1/ontology/classes` | 登录用户 / 管理员 | 实体类列表 / 新建 |
| PATCH | `/api/v1/ontology/classes/{id}` | 管理员 | 修改类 |
| GET/POST | `/api/v1/ontology/relations` | 登录用户 / 管理员 | 关系类型列表 / 新建 |
| POST | `/api/v1/extraction-tasks` | 贡献者+ | 触发抽取 `{parse_task_id}` |
| GET | `/api/v1/extraction-tasks/{id}` | 登录用户 | 抽取任务详情 |
| GET | `/api/v1/entities` | 登录用户 | 候选/已确认列表，支持 status/class/confidence 区间 |
| POST | `/api/v1/entities/{id}/confirm` | 贡献者+ | 确认，可带名称与属性修正 |
| POST | `/api/v1/entities/{id}/reject` | 贡献者+ | 驳回，需填理由 |
| POST | `/api/v1/entities/merge` | 贡献者+ | `{source_ids:[], target_id}` |
| POST | `/api/v1/entities/merge/undo` | 贡献者+ | 7 天内可撤销 |
| GET | `/api/v1/relations` | 登录用户 | 候选关系列表 |
| POST | `/api/v1/relations/{id}/confirm` | 贡献者+ | 确认 |
| POST | `/api/v1/relations/{id}/reject` | 贡献者+ | 驳回 |
| GET | `/api/v1/graph/subgraph` | 登录用户 | `entity_id` + `depth(1-3)` + `include_proposed` |
| GET | `/api/v1/documents/{id}/entities` | 登录用户 | 文档产出的实体 |
| GET | `/api/v1/entities/{id}/sources` | 登录用户 | 实体来源文档与块位置 |

### 审批

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET/POST | `/api/v1/review-flows` | 登录用户 / 管理员 | 审批流列表 / 新建 |
| PATCH | `/api/v1/review-flows/{id}` | 管理员 | 修改步骤 |
| POST | `/api/v1/documents/{id}/submit-review` | 贡献者+ | 提交审批 `{document_version_id, flow_id?}` |
| GET | `/api/v1/review-instances` | 登录用户 | 列表；`scope=mine` 返回待我审批 |
| GET | `/api/v1/review-instances/{id}` | 登录用户 | 详情含流转轨迹 |
| POST | `/api/v1/review-instances/{id}/approve` | 审批人/管理员 | `{comment?}` |
| POST | `/api/v1/review-instances/{id}/reject` | 审批人/管理员 | `{comment}` 必填 |
| POST | `/api/v1/review-instances/{id}/withdraw` | 提交人 | 撤回 |

### 异步与幂等

- 解析与抽取均返回 `202` + 任务标识，客户端轮询详情或用 `GET /api/v1/tasks/{id}/events` 长轮询
- Celery 任务以 `document_version_id + strategy_id`（解析）、`parse_task_id + ontology_version`（抽取）作为幂等键，重复投递不产生重复产物
- 所有写接口接受可选 `Idempotency-Key` 头，24 小时内重复请求返回首次结果

## 状态机汇总

```
document:      draft -> pending -> published -> archived
                 ^        |  \                    |
                 |        |   +--> rejected --+  |
                 |        |                   |  |
                 +--------+-------------------+  |
                 +------------------------------+

parse_task:    pending -> running -> succeeded
                  |          |  \
                  |          |   +--> failed --(retry)--> pending
                  +-> cancelled <---+

extraction_task: 与 parse_task 同构

entity:        proposed -> confirmed
                   |    \--> rejected
                   \--> merged --> (undo) proposed

review_instance: pending -> approved
                    |    \--> rejected
                    \--> withdrawn
```

所有迁移在 `domain/state_machines.py` 中以显式表定义，非法迁移统一抛 `40901`。

## 风险与取舍

| 风险 | 缓解 |
| --- | --- |
| PG 与 Neo4j 数据不一致 | 单向写入（业务状态先落 PG，图写入失败则任务标失败并可重放）；不引入分布式事务 |
| LLM 输出不合规或幻觉 | 结构化输出 + schema_validate 节点 + 修复循环上限 2 次；抽取结果一律进候选态，人工确认后才入正式图谱 |
| 大文件解析撑爆内存 | 流式上传、worker 独立容器、单任务内存与超时限制、超出即失败而非降级 |
| Celery 重复投递导致重复产物 | 幂等键 + `UNIQUE(parse_task_id, seq)` 兜底 |
| 解析产物膨胀 | block 按版本留存；历史版本默认保留 90 天，超期由清理任务归档原件与产物 |
| 审批流程被事中修改导致状态错乱 | 提交时冻结 `flow_snapshot`，进行中实例只读快照 |
| 审计日志量大 | 按月分区 + 保留 12 个月，超期归档到对象存储 |
| 前端一次拉取大文档块 | 块接口强制分页，单页上限 200 条，预览按页懒加载 |

## 迁移与上线

本期为首次上线，无历史数据迁移。初始化顺序：

```
1. 起 PostgreSQL / Neo4j / MinIO / Redis（docker-compose）
2. alembic upgrade head                 建 19 张表与索引
3. 执行 schema.cypher                   建图库约束与索引
4. 种子数据：workspace、4 个内置角色、初始管理员、各文档类型的默认解析策略
5. 启动 api 与 worker，验证 /healthz 与一条 PDF 的端到端解析
```

回滚策略：本期无数据兼容负担，回滚即停服 + `alembic downgrade base` 重建；上线后每次 schema 变更必须提供可执行的降级脚本。

## Open Questions

以下问题不影响本期规格与任务拆解，可在实施中确定：

- 解析引擎的具体选型（Unstructured / MinerU / PaddleOCR 组合）与各文档类型的默认参数，需用真实样本压测后定
- LLM 供应商与模型档位（影响抽取成本与一致性），需先用 50 份样本做质量对比
- 加密算法的具体参数（原件是否做服务端加密、密钥托管方式）
- 历史版本保留 90 天的默认值是否合适，需按实际存储成本调整


