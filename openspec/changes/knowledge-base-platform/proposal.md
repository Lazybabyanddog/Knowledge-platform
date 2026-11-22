# Proposal

## Why

企业内部文档分散在各处的网盘、IM 附件和业务系统里：格式杂乱、重复严重、无人对内容负责、过期了也没人知道。直接把原始文件丢给大模型做检索问答，得到的答案既不可控也不可追责。

需要先建一条**可治理的知识生产流水线**：文档进来后被可靠地解析、被抽取成结构化知识与图谱、被有权责的人审批后才生效。有了这条流水线，后续无论做检索问答、企业搜索还是外部 API 输出，都建立在可信的内容底座上。

本期只做"构建侧"（上传 → 解析 → 图谱 → 审批）与最基础的身份认证，把这条流水线跑通并固化为可扩展的领域模型。

## What Changes

- 新增身份与访问控制能力：账号密码登录、JWT 会话、角色（管理员/贡献者/审批人/只读）、接口级权限校验
- 新增文档管理能力：单文件与批量上传、格式与大小校验、对象存储、文档版本、元数据、软删除
- 新增解析流水线能力：可配置的解析策略、异步解析任务（Celery）、LangGraph 编排的解析图、解析产物（block）留存与失败重试
- 新增知识图谱能力：本体（类/关系类型）管理、LangGraph 驱动的实体关系抽取、抽取候选的人工确认/驳回/合并、子图查询
- 新增文档审批能力：提交审批、多步审批、批准/驳回/撤回、发布生效、版本状态机
- 新增统一审计日志，覆盖登录、上传、解析、图谱变更、审批动作
- 本期明确不做：反馈治理与治理工作台、自动巡检、检索与问答、rerank、SSO/SCIM、多租户 SaaS、外部连接器同步

## Capabilities

### New Capabilities

- `auth`: 账号登录、令牌签发与刷新、会话登出、当前用户信息、角色与接口级权限校验
- `document-management`: 文档上传（单/批量）、格式与大小校验、对象存储落盘、文档版本管理、元数据与列表查询、软删除与恢复
- `parsing-pipeline`: 解析策略的配置与版本化、异步解析任务的调度与状态机、解析产物的结构化留存、失败重试与质量评分
- `knowledge-graph`: 本体的类与关系类型管理、基于解析产物的实体关系抽取、候选知识的确认/驳回/合并、图谱与子图查询
- `document-approval`: 文档版本的提交审批、多步审批流、批准/驳回/撤回、发布生效与版本状态流转

### Modified Capabilities

无。本项目此前没有已存在的规格。

## Impact

- **新增代码**：`backend/`（FastAPI 应用、领域层、基础设施层、LangGraph 流水线、Celery worker）、`frontend/`（Vue3 + TypeScript 管理端）
- **新增依赖**：FastAPI、SQLAlchemy、Alembic、Celery、Redis、Neo4j Driver、LangChain、LangGraph、MinIO SDK、Pydantic v2；前端 Vue3、Vite、Pinia、Element Plus
- **新增基础设施**：PostgreSQL 16（业务主库）、Neo4j 5（知识图）、MinIO（对象存储）、Redis（缓存与任务队列）
- **对外接口**：新增 `/api/v1/auth`、`/api/v1/documents`、`/api/v1/parse-*`、`/api/v1/ontology`、`/api/v1/entities`、`/api/v1/review-*` 等 REST 接口
- **数据模型影响**：PostgreSQL 新建 19 张表；Neo4j 新建实体/关系/本体投影的节点与边模型
- **后续影响**：解析产物保留 `page_no` 与 `bbox` 锚点，为下一期的检索命中高亮与引用溯源预留位置，避免全量重跑
