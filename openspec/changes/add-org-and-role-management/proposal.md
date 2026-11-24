# Proposal

## Why

平台目前只有"用户 + 4 个硬编码角色"这一层身份维度：`app_user` 没有部门归属，上传的文档只存 `created_by` 的 UUID（前端拿不到姓名，更不知道属于哪个部门），角色权限写死在 `app/domain/policies.py` 的矩阵里，运营要给某个岗位开一种新角色只能改代码发版。

这带来三个具体问题：文档追溯只能看到一串 UUID；人员规模一上来，权限分配无法按组织结构自助维护；角色既是业务概念（审批步骤按角色找人）又是代码常量，无法在管理端查看和调整。

## What Changes

- **新增部门管理**：新建 `department` 表（`workspace_id` + 自关联 `parent_id` + `sort_order`），支持多层级部门树；提供 `/api/v1/departments` 的查询/新建/改名/移动/删除接口，删除受"有子部门或有人员"保护；前端新增「部门管理」页。
- **人员增加部门归属**：`app_user` 增加可空外键 `department_id`；用户管理支持按部门筛选、按部门展示、在新建/编辑表单里选择所属部门；部门筛选包含下级部门。
- **角色与权限数据化（BREAKING）**：新增 `permission`（权限目录）与 `role_permission`（角色-权限）两张表，把现有 4 个内置角色的权限矩阵迁移为数据；`GET/POST/PATCH/DELETE /api/v1/roles` 支持查看、新建自定义角色、调整角色权限、删除角色。权限判定改为从数据库读取（`Principal` 携带 `permissions`），不再依赖 `ROLE_PERMISSIONS` 常量。内置角色不可删除、`code` 不可修改，且必须保留管理类权限以防锁死。前端权限来源由本地常量改为 `/auth/me` 返回的 `permissions`。
- **审批流角色校验改为查库**：`review_service` 里"审批角色是否存在"的枚举校验改为查询 `role` 表，自定义角色才能被选作审批人。
- **上传记录带上人员与部门**：文档列表/详情返回 `created_by_name`（上传人姓名）与 `created_by_department_name`（上传人所属部门），文档版本同样返回上传人姓名；文档列表支持按上传人部门筛选；前端文档库新增「上传人 / 部门」列与部门筛选，文档详情展示上传人信息。

## Capabilities

### New Capabilities

- `organization`: 部门树的维护（新建、改名、移动、删除保护）与人员对部门的归属、按部门检索。
- `access-control`: 权限目录与角色-权限关系的数据化维护，角色 CRUD 约束，当前用户权限的下发与生效方式。
- `document-attribution`: 文档与文档版本的上传人身份（姓名、部门）在查询结果中的呈现与筛选。

### Modified Capabilities

无。`auth` 与 `document-management` 两个能力目前只存在于尚未归档的 `knowledge-base-platform` 变更中，`openspec/specs/` 下还没有基线文件，因此本次以新建能力承载增量；与其中既有需求文本的冲突见 Impact。

## Impact

**后端**
- 模型：新增 `app/infra/db/models/department.py`、`permission.py`（含 `RolePermission`），`role.py` 增加关系，`user.py` 增加 `department_id`，`models/__init__.py` 登记。
- 迁移：新增 Alembic revision（`down_revision = "a7c3f1b90e42"`），建 3 张表 + 给 `app_user` 加列，并在迁移内写入权限目录与内置角色权限的初始数据。
- 领域层：`domain/policies.py` 的 `ROLE_PERMISSIONS` 降级为"内置角色初始权限"种子，`Permission` 增加 `department:manage`、`role:manage`；`domain/enums.py` 的 `AuditAction` 增加部门/角色动作。
- 鉴权：`core/deps.py` 的 `Principal` 增加 `permissions`，`get_principal` 改为一次连表查询取"角色 + 权限"；`require_permission` 改为直接判断 `principal.permissions`。
- 接口：新增 `api/v1/departments.py`、`api/v1/roles.py`；`users.py` 支持 `department_id`；`auth.py` 的 `/me` 增加 `permissions`；`documents.py` 支持按部门筛选并返回上传人姓名/部门。
- 服务与仓储：新增 `department_service`/`department_repository`、`role_service`（角色与权限目录）；`user_service` 校验部门归属；`document_service`/`document_repository` 改为 join 取上传人姓名与部门；`review_service` 角色校验查库；`seed.py` 增加部门为空时的可选初始化与权限目录同步逻辑。

**前端**
- 新增视图 `DepartmentsView.vue`、`RolesView.vue`，路由与菜单（`router/index.ts`、`AppLayout.vue` 的 `ICONS`）。
- `UsersView.vue` 增加部门列、部门筛选与部门选择器，角色选项改为来自 `/roles`。
- `DocumentsView.vue` 增加上传人/部门列与部门筛选，`DocumentDetailView.vue` 展示上传人。
- `stores/auth.ts` 的 `permissions` 改为读 `/auth/me`；新增 `stores/roles.ts` 提供角色码到中文名的映射；`constants.ts` 的 `ROLE_PERMISSIONS` 退役，保留类型与权限分组标签。
- `api/types.ts`、`api/users.ts`、`api/documents.ts`、新增 `api/departments.ts`、`api/roles.ts`。

**行为与兼容**
- **BREAKING**：`ROLE_PERMISSIONS` 不再是权限事实来源，任何新增权限都必须落 `permission` 表；老客户端若依赖 `/auth/me` 只有 `roles` 字段仍可用，但本地推算权限会与实际不一致。
- 已登录用户无需重新登录：`/auth/me` 在每次守卫校验时刷新，`get_principal` 每请求查库。
- 与 `knowledge-base-platform` 变更中的 `auth` 规格存在文本冲突：该规格写"提供四种内置角色"，本次允许自定义角色。归档前需先归档该变更，或在合并时以本变更的角色能力为准并同步修订那段需求。
- 集成测试沿用现有 conftest（直连开发库，需 Postgres/Redis 可用），不引入独立测试库。
