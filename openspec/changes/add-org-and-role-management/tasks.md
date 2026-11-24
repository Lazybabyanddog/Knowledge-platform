# Tasks

## 1. 数据模型与迁移

- [x] 1.1 新增 `backend/app/infra/db/models/department.py`（`department`：`workspace_id`、`parent_id` 自关联、`name`、`sort_order` + `TimestampMixin`），并在 `models/__init__.py` 的导入与 `__all__` 中登记；验证：`python -c "from app.infra.db.models import Department"` 无报错
- [x] 1.2 新增 `backend/app/infra/db/models/permission.py`（`permission`：`code` 主键、`name`、`group_name`、`sort_order`；`role_permission`：`role_id` + `permission_code` 复合主键，双外键 CASCADE/RESTRICT 按设计），并登记到 `models/__init__.py`；验证：导入 `Permission`、`RolePermission` 成功且 `python -m alembic check` 只报待生成的迁移
- [x] 1.3 给 `AppUser` 增加 `department_id`（可空、FK `department.id` ON DELETE RESTRICT、建索引）；验证：模型导入成功，`created_at/updated_at` 等既有字段未变
- [x] 1.4 编写 Alembic revision（`down_revision = "a7c3f1b90e42"`）：建三张表、加 `app_user.department_id`（含顶层部门部分唯一索引 `(workspace_id, name) WHERE parent_id IS NULL`），并在迁移内硬编码写入权限目录与 4 个内置角色的 `role_permission`；验证：`alembic upgrade head` 成功，`alembic downgrade -1` 后重新 `upgrade head` 均无错，`alembic check` 报告无差异
- [x] 1.5 在 `backend/app/scripts/seed.py` 增加 `ensure_permissions`（只补缺失的权限目录行与缺失的内置角色授权，不删不覆盖）；验证：连续执行两次 `python -m app.scripts.seed`，第二次输出新增数为 0，且手工删除一条自定义角色的权限后重跑不会被恢复

## 2. 权限判定改为数据库驱动

- [x] 2.1 `backend/app/domain/policies.py`：新增 `Permission.DEPARTMENT_MANAGE`、`Permission.ROLE_MANAGE`，`ROLE_PERMISSIONS` 改名为 `BUILTIN_ROLE_PERMISSIONS` 并删除 `has_permission`/`permissions_for` 的运行时用法；验证：全仓搜索 `ROLE_PERMISSIONS|has_permission` 只剩种子与迁移引用，`python -c "from app.domain.policies import BUILTIN_ROLE_PERMISSIONS"` 通过
- [x] 2.2 `backend/app/core/deps.py`：`Principal` 增加 `permissions: frozenset[str]`，`get_principal` 用一次 `user_role → role_permission` 连表查询同时取角色码与权限码，`require_permission` 改为判断 `principal.permissions`；验证：`viewer` 账号调用 `POST /api/v1/documents` 返回 403，`admin` 返回 201
- [x] 2.3 `backend/app/infra/db/repositories/user_repository.py` 增加 `list_permissions(user_id)` 与 `list_permissions_map(user_ids)`（SQL 只写在此），并让 `list_roles` 与权限查询共用同一次 join；验证：`GET /api/v1/users` 返回的每条记录角色个数与库中 `user_role` 一致
- [x] 2.4 `schemas/auth.py` 的 `MeResponse` 与 `api/v1/auth.py` 的 `me()` 增加 `permissions: list[str]`；同步 `schemas/user.py` 的 `UserItem` 增加 `department_id`/`department_name` 占位并在服务层填充；验证：登录后 `GET /api/v1/auth/me` 返回 admin 的全部权限编码，且不含密码字段

## 3. 部门接口与服务

- [x] 3.1 新增 `backend/app/schemas/department.py`（`DepartmentItem`/`DepartmentCreateRequest`/`DepartmentUpdateRequest`/`DepartmentListResponse`），字段与设计 D9 对齐；验证：`python -c "import app.schemas.department"` 通过
- [x] 3.2 新增 `backend/app/infra/db/repositories/department_repository.py`（列表、按 id 取、子部门计数、人员计数、子树 id 集合 BFS、唯一性查询）与 `backend/app/services/department_service.py`（父链深度校验 ≤5、移动成环 400、同级重名 409、删除保护 409、审计记录）；验证：`pytest backend/tests/test_departments.py` 通过（覆盖同名、成环、超深、有子部门、有人员五类失败路径）
- [x] 3.3 新增 `backend/app/api/v1/departments.py`（GET/POST/PATCH/DELETE，`require_permission(DEPARTMENT_MANAGE)`，GET 额外放行 `user:manage`），注册到 `api/v1/router.py`；验证：无权限账号调用返回 403，管理员完成建/改/删闭环，`GET` 返回 `user_count`

## 4. 角色与权限接口

- [x] 4.1 新增 `backend/app/schemas/role.py`（`RoleItem` 含 `is_system`/`user_count`/`permissions`、创建与更新请求、`PermissionItem` 分组响应），编码校验使用 `^[a-z][a-z0-9_-]{1,31}$`；验证：非法编码（如 `Auditor`）提交返回 422/400
- [x] 4.2 新增 `backend/app/infra/db/repositories/role_repository.py`（列表含 `user_count`、按编码存在性、写权限集合、人员绑定数、`exists_codes`）与 `backend/app/services/role_service.py`（内置角色保护、编码不可改、删除前检查人员绑定与审批流引用、防锁死校验）；验证：`pytest backend/tests/test_roles.py` 通过（覆盖编码重复 409、删除内置 400、删除在用角色 409、清空管理权限 400）
- [x] 4.3 新增 `backend/app/api/v1/roles.py`（含 `GET /permissions`）并注册到 `api/v1/router.py`；验证：`GET /api/v1/roles` 返回 4 个内置角色及其权限，`GET /api/v1/permissions` 按分组返回 13 项
- [x] 4.4 打通"改权限即生效"链路：调整某自定义角色的权限后，使用该角色的已登录用户下一次调用对应接口返回 403/201（不需要重新登录）；验证：`pytest backend/tests/test_roles.py::test_permission_takes_effect_without_relogin` 通过

## 5. 人员与部门关联

- [x] 5.1 `schemas/user.py` 的 `UserCreateRequest`/`UserUpdateRequest`/`UserQuery` 增加 `department_id`，`UserItem` 返回 `department_name`；`user_service.py` 校验部门存在且同工作空间（不存在返回 400），并在 `_snapshot` 中带上部门名；验证：指定不存在部门返回 400，指定合法部门后列表返回部门名
- [x] 5.2 `user_repository.py` 的 `list_paginated` 支持 `department_id`（含下级，用 `department_repository.list_descendant_ids`）并返回 `department_name`；验证：按父部门筛选能命中子部门人员，`pytest backend/tests/test_users_department.py` 通过
- [x] 5.3 人员服务禁止把自己移出唯一的 admin 角色这类既有约束保持不回退，并补充"清除部门归属"用例；验证：`PATCH /api/v1/users/{id}` 传 `department_id: null` 后列表显示未分配

## 6. 审批流角色校验查库

- [x] 6.1 `backend/app/services/review_service.py` 删除 `ROLE_CODES` 常量，改为注入 `RoleRepository.exists_codes`，保留"第 N 步审批角色不存在"文案；验证：新增自定义角色后保存引用它的审批流成功，引用不存在编码返回 400
- [x] 6.2 审批实例步骤匹配复核：`_can_approve` 依赖 `principal.has_role(code)`，自定义角色编码可正常匹配；验证：`pytest backend/tests/test_review_custom_role.py` 通过（自定义角色人员可批准，只读人员 403）

## 7. 文档上传人归属（后端）

- [x] 7.1 `document_repository.list_paginated` 改为 `outer join app_user / department` 返回 `(Document, uploader_name, department_name)`，支持 `department_id`（含下级）条件，计数查询独立；验证：`GET /api/v1/documents?department_id=<技术部>` 只返回上传人属于技术部或其中子部门人员的文档
- [x] 7.2 `schemas/document.py` 的 `DocumentItem` 增加 `created_by_name`/`created_by_department_id`/`created_by_department_name`，`DocumentVersionItem` 增加 `created_by_name`，`DocumentQuery` 增加 `department_id`；`document_service.document_item()` 从 join 结果填充，保留原 `created_by` 字段；验证：`GET /api/v1/documents` 与 `GET /api/v1/documents/{id}` 均返回姓名与部门，未分配部门时部门字段为 `null`
- [x] 7.3 详情与版本列表补齐上传人姓名（`get`/`list_versions` 同样 join `app_user`）；验证：`GET /api/v1/documents/{id}/versions` 每个版本都有 `created_by_name`，不同上传人的版本显示各自姓名
- [x] 7.4 写集成测试覆盖归属语义：上传→转移负责人后上传人不变、人员换部门后列表部门变化、停用上传人仍显示姓名；验证：`pytest backend/tests/test_document_attribution.py` 通过

## 8. 前端权限来源与共享角色字典

- [x] 8.1 `api/types.ts` 的 `MeResponse` 增加 `permissions: string[]`，`UserItem` 增加 `department_id`/`department_name`，`DocumentItem`/`DocumentVersion` 增加上传人字段；验证：`npx vue-tsc --noEmit` 无类型错误
- [x] 8.2 `stores/auth.ts` 改为直接使用 `user.permissions`（缺失时触发一次 `fetchMe`，仍缺失则清空登录态），`constants.ts` 删除 `ROLE_PERMISSIONS`/`permissionsOf`，保留 `Permission` 类型、权限分组与中文标签；验证：新增 `frontend/tests/permissions.test.ts` 覆盖"有权限/无权限/缓存缺字段"三种情况，`npx vitest run` 通过
- [x] 8.3 新增 `api/departments.ts`、`api/roles.ts`、`stores/roles.ts`（`code → {name, permissions}` 缓存 + `ensureLoaded`），并把 `AppLayout.vue` 顶栏角色文案、`UsersView.vue` 角色列、`ReviewFlowsView.vue` 审批角色下拉改用该 store；验证：新建自定义角色后刷新页面，人员编辑与审批流下拉都能看到中文名

## 9. 前端页面：部门管理与角色管理

- [x] 9.1 新增 `views/DepartmentsView.vue`：树形表格（`row-key` + `tree-props`）、新建/新建子部门/编辑/删除、显示人员数，删除失败展示后端中文提示；验证：界面完成建三级部门→移动→删除有人员部门被拒的闭环
- [x] 9.2 新增 `views/RolesView.vue`：角色列表（内置角色标记、人员数）、新建自定义角色、按分组勾选权限、内置角色禁止删除/改编码；验证：新建 `auditor` 角色并勾选两项权限后，人员编辑页可选到该角色
- [x] 9.3 在 `router/index.ts` 增加 `departments`（`permission: 'department:manage'`）与 `roles`（`permission: 'role:manage'`）路由，分组「系统」，并在 `AppLayout.vue` 的 `ICONS` 补 `OfficeBuilding`/`Key`；验证：用管理员看到两个新菜单，用只读账号访问被重定向到 `/403`
- [x] 9.4 在 `README.md` 补充组织与角色管理的入口说明、新增接口清单与"权限来源改为数据库"的说明；验证：按 README 的迁移/seed 步骤在干净库启动后能进入两个新页面

## 10. 前端页面：人员与文档展示

- [x] 10.1 `UsersView.vue` 增加「部门」列与部门筛选（`el-tree-select`），新建/编辑弹窗增加部门选择器，角色选项改为来自 `roleApi`；验证：列表显示部门名、按上级部门筛选能出现子部门人员、新建人员可指定部门
- [x] 10.2 `DocumentsView.vue` 增加「上传人」「部门」列与部门筛选条件，`query` 增加 `department_id`；验证：列表显示上传人姓名与部门，未分配显示"未分配"，切换部门筛选后分页总数同步变化
- [x] 10.3 `DocumentDetailView.vue` 展示上传人与所属部门，版本历史每行显示该版本上传人姓名；验证：详情页头部与版本表都能看到姓名，转移负责人后上传人显示不变

## 11. 集成验证

- [ ] 11.1 端到端走查：建部门树 → 建人员并归属 → 建自定义角色并授权 → 上传文档 → 文档库看到上传人/部门 → 按部门筛选命中 → 收回文档写入权限后该用户上传返回 403；验证：整条链路一次走通，审计表能查到部门与角色相关动作
- [x] 11.2 回归既有能力：登录/刷新、文档上传与解析触发、本体管理、审批流保存与审批各跑一遍；验证：`pytest backend/tests` 与 `npx vitest run` 全绿，无新增失败用例

## Workflow follow-up

- 归档前处理与 `knowledge-base-platform` 变更中 `auth` 规格的文本冲突（"提供四种内置角色" vs 支持自定义角色）。
- 归档后确认 `openspec/specs/organization`、`access-control`、`document-attribution` 三个能力文件生成正确。
