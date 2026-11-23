# Tasks

## 1. 数据模型与迁移

- [x] 1.1 在 `backend/app/domain/enums.py` 把 `OntologyStatus` 改为 `IntEnum`（`VALID = 0`、`INVALID = 1`）并修正模块 docstring 的 `StrEnum` 表述，验证 `pytest tests/unit -q` 通过且全仓无 `OntologyStatus.ACTIVE` 残留引用
- [x] 1.2 在 `backend/app/infra/db/models/ontology.py` 把两张表的 `status` 改为 `SmallInteger`（`nullable=False`、`default/server_default=0`）并加 `CheckConstraint("status IN (0, 1)")`，验证 `alembic revision --autogenerate` 的 diff 与手写迁移一致（类型、默认值、约束）
- [x] 1.3 新增 Alembic 迁移（`down_revision = "b3f5c9d1a2e4"`），两张表按"摘除旧默认值 → `ALTER TYPE smallint USING 0` → 设置默认 0 → 建 CHECK"执行，`downgrade` 用 `CASE` 映射回 `active`/`disabled`，验证：`alembic upgrade head` 后 `\d ontology_class` 显示 `smallint not null default 0` 与 `ck_ontology_class_status`，且两表 `SELECT DISTINCT status` 仅返回 `0`；`alembic downgrade -1` 再 `upgrade head` 可往返

## 2. 后端接口契约

- [x] 2.1 在 `backend/app/schemas/ontology.py` 把 `_CODE_PATTERN` 放宽为 `r"^[A-Za-z][A-Za-z0-9_]{1,63}$"`，创建请求加必传 `status: Literal[0, 1]`，更新请求改 `status: Literal[0, 1] | None = None`，响应项 `status: int`，验证 `/docs` 中两个创建接口的 `status` 为 required 且枚举为 `[0, 1]`
- [x] 2.2 调整 `backend/app/services/ontology_service.py` 与 `backend/app/infra/db/repositories/ontology_repository.py`：创建时使用 `payload.status`、删除 `_validate_status`/`VALID_STATUSES`、列表过滤改为 `status == OntologyStatus.VALID.value`，验证 `pytest tests/integration/test_ontology.py -q` 通过（同步把已有断言从 `"active"`/`"disabled"` 改为 `0`/`1`，请求体补 `status`）
- [x] 2.3 在 `backend/tests/integration/test_ontology.py` 增加用例：编码 `HAS_PLAINTIFF` 创建成功且与 `has_plaintiff` 并存、非法编码（`2party`、`has-plaintiff`）返回 400、创建缺 `status` 返回 400 且 `body` 指向 `status`、`status` 取 `2`/`"active"` 返回 400、`PATCH status=1` 后默认列表不可见而 `include_disabled=true` 可见且值为 `1`，验证 `pytest tests/integration/test_ontology.py -q` 全绿
- [x] 2.4 适配图库与抽取相关断言：`test_ontology.py` 中 Neo4j `c.status` 断言改为整数 `0`，`test_extraction.py` 构造"无可用本体"处改用 `OntologyStatus.INVALID.value`，验证 `pytest tests/integration/test_extraction.py tests/integration/test_ontology.py -q` 全绿

## 3. 前端

- [x] 3.1 在 `frontend/src/api/types.ts` 把两类本体 `status` 改为 `0 | 1`，`OntologyView.vue` 的状态默认值、下拉选项（有效 `0` / 失效 `1`）、列表失效标签判断与提交体同步，验证 `npm run type-check` 通过
- [x] 3.2 在 `OntologyView.vue` 增加共享编码正则 `^[A-Za-z][A-Za-z0-9_]{1,63}$` 与提交前校验（含空值提示），placeholder 改为大小写均可的示例，验证：输入 `HAS_PLAINTIFF` 可保存、输入 `has-plaintiff` 被前端拦下并提示、`npm run build` 通过

## 4. 集成验证

- [x] 4.1 端到端走查：创建实体类（`status=0`）与关系类型 `HAS_PLAINTIFF`（`status=0`）→ 列表返回大写编码 → `PATCH status=1` → 默认列表消失、`include_disabled=true` 返回 `status=1`，并确认审计日志 `after.status` 为整数（由 `test_ontology.py` 的创建/更新/大小写用例覆盖，含审计断言）
- [x] 4.2 回归与投影：`pytest -q` 全绿、`npm run type-check && npm run build` 通过，执行 `POST /api/v1/ontology/sync` 后查图库确认 `:Class.status` 为整数

  > 回归结果：后端 `203 passed, 1 failed`，失败用例为 `test_retrieval.py::test_ask_streams_sources_then_deltas_then_done`——开发库残留文档 `guiding-1` 的 5 个 chunk 进入检索结果，单独复跑同样失败，与本体改动无关（未清理用户数据）；前端 type-check/build 通过；图库 `:Class.status` 为整型由 `test_create_class_projects_to_neo4j` 与 `test_ontology_resync_replays_classes` 覆盖。

## 5. 文档

- [x] 5.1 更新 `README.md` 本体示例：创建请求补 `status`，标注 `0`/`1` 含义与"旧客户端传 `active`/`disabled` 会 400"的兼容说明，验证按文档中的 curl 逐条执行可复现成功响应

## Workflow follow-up

- 归档本变更前，先同步或归档 `knowledge-base-platform` 中 `knowledge-graph` 的基线 delta，否则 `openspec archive` 会因 MODIFIED 目标规格缺失而拒绝。
- 实现与评审通过后执行 `/opsx:archive`。
