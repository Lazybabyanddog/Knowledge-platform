# Design

## Context

- 鉴权链路：`get_principal`（`backend/app/core/deps.py`）每请求按 `user_id` 查库取角色码，令牌里的角色只是快照、不参与判定；`require_permission` 是唯一的接口级权限闸门，全部判定集中在 `deps.py` 一处。
- 权限事实来源当前是代码：`backend/app/domain/policies.py` 的 `ROLE_PERMISSIONS`，前端 `src/constants.ts` 复制了一份同样矩阵用于菜单与按钮。
- 角色已落库（`role` 表，含 `is_system`），但只有 seed 会写；`user_role` 是用户-角色关联表。
- 文档侧：`document.created_by`（首次上传人，不可变）与 `document.owner_id`（负责人，可编辑）分开；`document_version.created_by` 记录各版本上传人；列表查询目前只 `select(Document)`，不 join 用户表。
- 分层约定：SQL 只写在 `infra/db/repositories/`，业务在 `services/`，路由只做参数校验；新模型必须登记到 `infra/db/models/__init__.py`；迁移 head 为 `a7c3f1b90e42`。
- 组织维度目前只有 `workspace`，实际是单工作空间部署；用户只能停用，没有删除接口。

## Goals / Non-Goals

**Goals:**

- 权限目录与角色-权限关系落库，`ROLE_PERMISSIONS` 常量退化为初始种子，不再参与运行时判定。
- 部门以邻接表建模，支持 5 层、移动与删除保护，人员单部门归属。
- 文档列表/详情/版本以查询期 join 的方式呈现上传人姓名与部门。
- 角色与权限的变更下一个请求即生效，不需要重新登录，也不需要重签令牌。
- 迁移可回滚，且不破坏既有 4 个内置角色的语义（admin 仍全量）。

**Non-Goals:**

- 不做多部门归属、部门负责人/编制、部门层级数据权限（文档 ACL 仍按现有 `document_acl` 机制）。
- 不做人员批量导入导出、组织架构同步（LDAP/钉钉等）。
- 不做历史快照式的"上传当时部门"：归属按当前档案实时解析。
- 不做角色权限的分页/搜索，角色数量在数十量级。

## Decisions

### D1 权限事实来源改为数据库

新增 `permission`（`code` 唯一、`name`、`group_name`、`sort_order`）与 `role_permission`（`role_id` + `permission_code` 复合主键，`permission_code` 外键指向 `permission.code`）。`domain/policies.py` 保留 `Permission` 枚举作为可判定能力的清单，`ROLE_PERMISSIONS` 改名为 `BUILTIN_ROLE_PERMISSIONS`，仅用于迁移与 seed 的初始写入。

`Principal` 增加 `permissions: frozenset[str]`，`get_principal` 用一次连表查询（`user_role → role_permission`）同时拿到角色码与权限码，`require_permission` 直接判断 `principal.permissions` 是否包含所需能力。`has_permission(roles, p)` 删除调用点后一并删除，避免出现第二条判定路径。

**备选**：
- 角色权限放 `role.permissions` JSONB：省两张表，但权限编码无约束、无法直接做权限目录 UI、和"权限目录"这条需求错位。
- 只允许自定义角色配权限、内置角色保持代码矩阵：出现双事实来源，`has_permission` 与 DB 两条路径必须长期共存，最容易在新增权限时漏改。

**新增权限**：`department:manage`、`role:manage`。`_ADMIN = frozenset(Permission)` 会自动包含新成员，admin 仍是全量。

### D2 部门用邻接表，应用层校验环与深度

`department`：`id`、`workspace_id`（FK `workspace`，RESTRICT）、`parent_id`（自关联 FK，RESTRICT，可空）、`name`(64)、`sort_order`(int，默认 0)、`created_at/updated_at`。唯一约束 `(workspace_id, parent_id, name)`；`parent_id` 为 NULL 时用部分唯一索引 `(workspace_id, name) WHERE parent_id IS NULL` 表达"顶层不重名"。

- 深度上限 5，在服务层沿父链上溯计算；移动时若目标节点在当前节点的子树内则 400（同样沿父链判断）。
- 删除：先查子部门与人员归属，给出中文 409；数据库 FK RESTRICT 兜底。
- 列表：一次全量查询（部门数量小），返回扁平行 + `parent_id` + `user_count`，前端组树；避免递归 SQL。
- "含下级"筛选：仓储层先取 `(id, parent_id)` 全量对，在内存 BFS 出子树 id 集合，再拼成 `IN` 条件。比递归 CTE 更简单，规模上完全够用。

**备选**：物化路径（`path` 列）或闭包表 —— 查询更强，但写入要维护冗余路径，且现有数据为零，收益不抵复杂度。

### D3 人员-部门为单值可空外键

`app_user.department_id`（FK `department.id`，`ON DELETE RESTRICT`，可空，建索引）。可空保证了存量用户与"不归属任何部门"的人员都合法；RESTRICT 让删除保护在数据库层也成立。

`UserCreateRequest`/`UserUpdateRequest` 增加 `department_id: str | None`，`UserQuery` 增加 `department_id`，`UserItem` 增加 `department_id` 与 `department_name`。服务层校验部门与用户同工作空间。

**备选**：多对多 `user_department` —— 展示"部门"这一列时语义变成"主部门"，需要额外字段，当前无需求。

### D4 上传人身份用查询期 join，不加冗余快照列

`DocumentRepository.list_paginated` 改为返回 `(Document, uploader_name, department_name)` 元组列表：

```python
select(Document, AppUser.display_name, Department.name)
    .outerjoin(AppUser, AppUser.id == Document.created_by)
    .outerjoin(Department, Department.id == AppUser.department_id)
```

计数查询保持独立；`get`/`get_versions` 同理补 join 取上传人姓名。`DocumentItem` 增加 `created_by_name`、`created_by_department_id`、`created_by_department_name`，`DocumentVersionItem` 增加 `created_by_name`；`document_item()` 组装时填充，`created_by`（UUID）保留不动以兼容既有前端类型。

**理由**：用户没有删除接口（只有停用），join 必定命中；姓名/部门改名后历史记录同步更新，符合"当前档案"语义。**备选**：仿 `audit_log.actor_name` 存快照 —— 适合合规审计，但需要回填历史数据，且改名后同一人在不同文档上显示不一致；如后续要"上传当时的部门"，再加独立快照列即可，不影响本次结构。

### D5 角色的内置保护与防锁死

- `is_system = true` 的角色：可改名、改描述、改权限，但 `code` 不可改、不可删除。
- 自定义角色：`code` 匹配 `^[a-z][a-z0-9_-]{1,31}$`，创建后同样不可改（`code` 会被审批流与令牌引用）。
- 删除前置：没有任何 `user_role` 绑定，且没有被任何 `review_flow` 的步骤引用（流程数量少，服务层加载后在 Python 里检查，避免 JSONB 包含查询的方言细节）。
- 防止管理入口被清空：任何一次角色权限变更后，若系统中不再存在"同时具备 `role:manage` 与 `user:manage`"的角色，返回 400（对内置 admin 角色同样适用，避免被改成空集）。

**备选**：完全禁止编辑内置角色的权限 —— 更保守，但与"角色管理"的运营诉求相悖；上面的兜底校验已能防止锁死。

### D6 审批流角色校验查库

`review_service` 的 `ROLE_CODES = {role.value for role in RoleCode}` 改为 `RoleRepository.exists_codes(codes)`；保存流程时角色不存在返回 400（错误文案保留"第 N 步审批角色不存在"）。`principal.has_role(code)` 本来就是按编码比对，自定义角色天然可用。

### D7 迁移与种子

新增一个 Alembic revision（`down_revision = "a7c3f1b90e42"`）：

1. 建 `department`、`permission`、`role_permission`；
2. `app_user` 加可空列 `department_id` + FK + 索引（PG 11+ 加可空无默认列是元数据操作，不重写表）；
3. 数据迁移：插入权限目录 13 行（含新增两项）与 4 个内置角色的 `role_permission`，权限清单在迁移内**硬编码**，不 import 应用枚举，保证历史迁移可重放；
4. downgrade 逆序 drop。

`seed.py` 增加 `ensure_permissions`：只补缺失的权限目录行与缺失的内置角色授权，不删除、不覆盖运营调整过的授权，保持幂等。部门不做默认种子，保持空树。

### D8 前端权限与角色字典

- `/auth/me` 增加 `permissions: string[]`；`stores/auth.ts` 的 `permissions` 改为 `computed(() => new Set(user.permissions ?? []))`，`constants.ts` 的 `ROLE_PERMISSIONS` 与 `permissionsOf` 删除，保留 `Permission` 类型与权限分组/中文标签（供角色页渲染分组复选框）。
- 新增 `stores/roles.ts`：`roleApi.list()` 结果缓存为 `code → {name, permissions}` 的响应式 map，首次使用时加载；`AppLayout` 顶栏角色文案、`UsersView` 角色列/复选框、`ReviewFlowsView` 审批角色下拉统一改用它，`ROLE_LABELS` 仅作内置角色的兜底常量。
- 兼容：`localStorage` 里的旧 `MeResponse` 没有 `permissions` 字段，路由守卫在 `user` 为空时才拉取，会自然补齐；若守卫跳过拉取，权限集合为空导致菜单变少 —— 处理方式是 `auth.has` 在 `user.permissions == null` 时视为未加载（返回 false 并触发一次 `fetchMe`），实施时在 store 初始化里判断一次。

### D9 API 契约（摘要）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/departments` | `department:manage` 或 `user:manage` | 扁平行，含 `parent_id`、`user_count` |
| POST | `/api/v1/departments` | `department:manage` | `{name, parent_id?, sort_order?}` |
| PATCH | `/api/v1/departments/{id}` | `department:manage` | 改名/移动/排序 |
| DELETE | `/api/v1/departments/{id}` | `department:manage` | 受保护删除 |
| GET | `/api/v1/roles` | `role:manage` | 角色列表（含 `permissions`、`user_count`、`is_system`） |
| POST | `/api/v1/roles` | `role:manage` | `{code, name, description?, permissions[]}` |
| PATCH | `/api/v1/roles/{id}` | `role:manage` | 名称/描述/权限；`code` 不可改 |
| DELETE | `/api/v1/roles/{id}` | `role:manage` | 受保护删除 |
| GET | `/api/v1/permissions` | `role:manage` | 按 `group_name` 分组返回 |
| GET | `/api/v1/users` | `user:manage` | 新增 `department_id` 查询参数（含下级） |
| GET | `/api/v1/documents` | `document:read` | 新增 `department_id`（含下级）；返回上传人姓名/部门 |

响应仍走 `{code,message,data}` 信封；错误统一用 `BadRequestError(400)`/`NotFoundError(404)`/`ConflictError(409)`/`ForbiddenError(403)`。

### D10 审计

`AuditAction` 增加 `DEPARTMENT_CREATE/UPDATE/DELETE`、`ROLE_CREATE/UPDATE/DELETE`、`ROLE_PERMISSION_UPDATE`；部门快照记录 `name` 与父部门名称，角色快照记录 `code` 与权限集合。`AuditService` 已带 `actor_name`，无需改动表结构。

## Risks / Trade-offs

- **权限即时生效会让前端按钮滞后**：用户停留在页面时权限被收回，点击才 403。→ 保持现有 `errorMessage` 统一提示；路由守卫每次进入页面都会校验 `auth.user`，不额外做轮询。
- **角色被删除后在途审批无法推进**：审批实例的 `flow_snapshot` 里存的是角色编码，角色消失后就没人能审。→ 删除前置检查 `review_flow` 引用；已提交实例若已进入无法匹配的角色，按现有"转人工/管理员处理"思路处理，本次不新增流程。
- **部门调整导致历史文档归属"漂移"**：文档列表显示的是上传人当前部门。→ 已写入 `document-attribution` 规格作为明确语义；若将来需要合规口径，再加 `document_version.department_snapshot` 列，结构上不冲突。
- **防锁死校验不足**：只保证"存在一个具备两项管理权限的角色"，若该角色没有绑定任何用户仍会锁死。→ 校验时同时要求该角色至少绑定一名启用的用户。
- **一次全量 join 影响文档列表性能**：`app_user`/`department` 都按主键 join，且 `created_by` 已建索引；分页后行数受 `page_size` 限制（≤100）。→ 若后续变慢，把上传人信息改为批量二次查询（先查文档，再 `IN` 查用户与部门）。
- **前端本地缓存 `MeResponse`**：旧缓存无 `permissions`。→ store 初始化时若缺少该字段则清空 `user`，由路由守卫重新拉取。
- **权限目录与代码枚举漂移**：新增权限时若忘了登记 `permission` 表，角色页看不到但接口可判定。→ `seed.ensure_permissions` 每次启动补齐目录；`validate` 阶段不强制，靠 seed 幂等兜底。

## Migration Plan

1. 生成并执行迁移：`cd backend && alembic upgrade head`；回滚 `alembic downgrade -1`（新表无外部引用，可空列 drop 安全）。
2. 执行 `python -m app.scripts.seed` 补齐权限目录（迁移已写入初始数据，seed 仅幂等兜底）。
3. 部署顺序：数据库迁移 → 后端 → 前端。后端先上不影响旧前端（`/auth/me` 只是多返回字段）；新前端依赖 `permissions` 字段，故后端先行。
4. 回滚策略：回滚前端即恢复本地矩阵（旧版本代码仍在）；回滚后端需同时回滚迁移，角色/部门数据会丢失，因此回滚前先备份 `department`、`role`、`role_permission` 三表。

## Open Questions

- 部门是否需要"负责人"字段与人员调动历史？当前非目标，若需要可在 `department` 上加 `leader_id` 而不影响本设计。
- 是否要把部门用于文档的行级可见性（部门内可见）？属于 ACL 范畴，留待后续变更。
