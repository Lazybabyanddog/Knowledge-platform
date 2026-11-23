# Design

## Context

动机见 `proposal.md`。实现现状（均为只读勘察结论）：

- `backend/app/schemas/ontology.py`：`_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"` 同时用于实体类与关系类型的创建请求；创建请求没有 `status` 字段，Pydantic 默认 `extra=ignore`，请求里的 `"status"` 被丢弃；`OntologyClassUpdateRequest.status` / `OntologyRelationUpdateRequest.status` 是裸 `str | None`。
- `backend/app/domain/enums.py`：`OntologyStatus` 是 `StrEnum`（`active`/`disabled`），模块 docstring 声明"全部继承 StrEnum"；该枚举被模型默认值、仓储过滤、服务校验引用。
- 模型 `status` 为 `String(16) NOT NULL DEFAULT 'active'`，无 `CHECK`；`(workspace_id, code)` 唯一，`code` 是 `String(64)`。
- `OntologyRepository.list_classes/list_relations` 在 `include_disabled=False` 时过滤 `status == 'active'`；`ontology_schema_snapshot` 复用该默认过滤，因此"失效即不参与抽取"已成立。
- 服务层 `_validate_status` 校验字符串白名单，创建路径硬编码 `OntologyStatus.ACTIVE.value`；响应 `class_item/relation_item` 直接透传 `row.status`。
- 图库投影 `infra/graph/ontology_projection.py` 把 `status` 原样写入 `:Class` 属性；除投影外，代码中没有按 `Class.status` 过滤的 Cypher（`entity_repository` 只按 `workspace_id + code` 关联）。
- 前端 `OntologyView.vue` 中状态下拉值为 `"active"`/`"disabled"`，标签判断 `row.status !== 'active'`；编码输入框只有 placeholder（`org` / `works_at`），无任何前端校验。
- 迁移链 head 为 `b3f5c9d1a2e4`（`document_chunk_tables`），`ontology_*` 表由 `429dd97f379a` 创建。
- 测试：`backend/tests/integration/test_ontology.py` 创建请求未带 `status`、断言 `"active"` 与 Neo4j 字符串属性；`test_extraction.py` 用 `row.status = "disabled"` 构造"无可用本体"。前端无组件测试框架，只有 `frontend/tests/sse.test.ts`，前端约束以 `npm run type-check` + `npm run build` 为准。

## Goals / Non-Goals

**Goals:**

- 编码校验只约束"字母开头 + 字母数字下划线"，大小写均可，并把同一规则显式落到前端，消除"后端 400、前端无提示"的错配。
- `status` 成为有类型契约的字段：请求必传、取值 `0`/`1`、响应整数、数据库 `smallint + CHECK + NOT NULL + DEFAULT 0`。
- 存量数据一次性收敛到 `0`，升级后无需人工修数。
- 变更可回滚，回滚路径与前端版本对应关系明确。

**Non-Goals:**

- 不做编码大小写归一/自动大写，不做"大小写不敏感唯一"。
- 不改抽取侧的编码匹配策略（保持精确匹配）。
- 不改 `include_disabled` 的对外参数名与语义，不改列表排序、版本号规则。
- 不为 `code` 增加数据库 `CHECK` 约束（存量数据形态未审计，见 Open Questions）。
- 不调整 `knowledge-base-platform` 变更内其他规格文本。

## Decisions

### D1：编码正则放宽为大小写均可，且不自动转大写

- 后端：`_CODE_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{1,63}$"`，实体类与关系类型继续共用；长度语义不变（首字符 1 + 后续 1–63，即总长 2–64）。
- 前端：新增共享常量 `CODE_PATTERN = /^[A-Za-z][A-Za-z0-9_]{1,63}$/`，在 `saveClass` / `saveRelation` 提交前校验，非法时提示并中止；placeholder 改为"字母开头，如 org、HAS_PLAINTIFF"。用户输入不做 `toUpperCase()`。
- 理由：编码是跨版本稳定标识，原样存储才能与唯一键、图库 `:Class.code`、抽取提示词中的编码一一对应；自动大写会静默改写入参，并使"编辑时展示的编码"与用户输入不一致。此前讨论过的"输入自动转大写"随"纯大写规则"一起取消。
- 备选：后端 `strip().upper()` 归一。被否：会让 `HAS_PLAINTIFF` 与 `has_plaintiff` 无法共存，且与既有小写编码的历史数据混淆；`strip()` 仍保留在服务入口（现有行为）。
- 备选：正则改为大小写不敏感但存储转小写。被否：同样破坏"原样展示"，且需回填存量。

### D2：`status` 用 `Literal[0, 1]` + `IntEnum`，不使用 `int + ge/le`

- `OntologyStatus` 改为 `class OntologyStatus(IntEnum): VALID = 0; INVALID = 1`，并修正 `domain/enums.py` 模块 docstring 中"全部继承 StrEnum"的表述。
- 创建请求：`status: Literal[0, 1]`（无默认值 → 必传）；更新请求：`status: Literal[0, 1] | None = None`；响应项：`status: int`。
- 理由：`Literal` 让 OpenAPI 直接暴露枚举、错误信息明确指向 `status`，且天然拒绝 `"active"`、`true`、`2`；`int + Field(ge=0, le=1)` 会在 OpenAPI 里丢失枚举语义，还要额外处理布尔值被当作 0/1 的宽松转换。
- 服务层删除 `_validate_status` 与 `VALID_STATUSES`（Pydantic 已是唯一真值来源），创建时 `status=payload.status` 取代硬编码 `ACTIVE`；更新时仍在 `payload.status is not None` 分支内赋值。
- 备选：保留字符串 `"0"`/`"1"` 只加必传。被否：与"0 有效 / 1 失效"的语义距离远，且数据库仍是无约束字符串。

### D3：数据库列改 `smallint`，迁移一步完成"转类型 + 存量置 0"

模型：

- `status: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=OntologyStatus.VALID.value, server_default="0")`
- `__table_args__` 增加 `CheckConstraint("status IN (0, 1)", name="ck_ontology_class_status")` / `ck_ontology_relation_status`。

迁移（新 revision，`down_revision = "b3f5c9d1a2e4"`，两张表同样三步）：

1. `op.alter_column(<table>, "status", server_default=None, existing_type=sa.String(16))`——先摘掉 `'active'` 默认值，否则类型转换会尝试把旧默认值转成 `smallint` 而失败。
2. `op.alter_column(<table>, "status", type_=sa.SmallInteger(), existing_type=sa.String(16), nullable=False, postgresql_using="0")`——`USING 0` 同时完成类型转换与"所有存量行置 0"，正是本次要的语义。
3. `op.alter_column(<table>, "status", server_default="0")` + `op.create_check_constraint(...)`。

`downgrade`：删 `CHECK` → 去默认 → `ALTER TYPE varchar(16) USING CASE WHEN status = 1 THEN 'disabled' ELSE 'active' END` → 恢复 `server_default='active'`。降级会丢失"原本停用"的信息（迁移时已全部置 0），属于已知信息损失。

理由与备选：

- 备选：新增 `is_valid boolean` 列再弃用 `status`。被否：对外字段名与语义都要改，改动面更大，且用户明确要求沿用 `status`。
- 备选：`USING CASE WHEN status = 'disabled' THEN 1 ELSE 0 END` 保留旧停用语义。被否：与"所有数据 status 都改为 0"的明确要求冲突；代价（原停用定义恢复有效）写进风险与发布说明。
- 备选：先加临时列、回填、再改名。表体量小且无并发写入窗口要求，直接 `ALTER` 更简单。

### D4：过滤与投影只改取值，不改契约

- `OntologyRepository` 两个列表查询的过滤改为 `status == OntologyStatus.VALID.value`；`include_disabled=True` 的语义变为"包含 `status = 1`"。
- `ontology_schema_snapshot` 不变，继续使用默认过滤，因此"失效本体不参与抽取"无需额外代码。
- `ontology_projection._row` 继续透传 `row.status`，值变为整数；`project_class_safe` / `project_workspace` 无需改逻辑。发布后需要重放一次，让历史节点从字符串 `"active"` 变为整数 `0`。
- 审计 `before/after` 中的 `status` 自然变成整数，不需要额外转换。

### D5：前端状态与校验

- `frontend/src/api/types.ts`：`OntologyClass.status`、`OntologyRelation.status` 改为 `0 | 1`。
- `OntologyView.vue`：`classForm.status`、`relationForm.status` 类型 `0 | 1`，新建默认 `0`；下拉选项 `:value="0"`（有效）/ `:value="1"`（失效）；列表标签条件改为 `row.status === 1` 显示"失效"。
- 编码校验复用 D1 的 `CODE_PATTERN`；两处 `el-input` 的 placeholder 同步为大小写均可的示例。
- 创建与编辑仍整体提交 `status`（现状即如此），只是值从字符串变数字；PATCH 时 `status` 仍随 body 提交，由后端校验。

## Risks / Trade-offs

- **[BREAKING] 旧客户端仍发 `"active"`/`"disabled"` 或不带 `status`** → 后端一律 400，且 `PATCH` 不再接受字符串。缓解：前后端同版本发布；发布说明给出字段迁移对照（`active`→`0`、`disabled`→`1`）。
- **存量停用定义被迁移为有效，抽取范围扩大** → 发布说明明确告知；如业务上必须保留，改迁移为 `CASE` 分支即可（属计划外决策，不在本次实现）。
- **Neo4j 历史 `:Class.status` 仍为字符串** → 现有代码没有按该属性过滤的查询，影响限于属性一致性；发布后执行 `POST /api/v1/ontology/sync`，或等待每个类下一次写入时刷新。
- **编码大小写敏感导致抽取丢弃**：LLM 输出 `has_plaintiff` 而本体为 `HAS_PLAINTIFF` 时，`validators.py` 会判 `relation_code_unknown` 并丢弃。缓解：本体编码在抽取提示词中原样注入，规则要求照抄；若后续需要容错，另立变更做"大小写不敏感匹配 + 冲突检测"，避免把两个不同编码静默合并。
- **MODIFIED delta 的归档前置**：`openspec/specs/knowledge-graph/spec.md` 尚不存在（基线在 `knowledge-base-platform` 内），`openspec validate` 通过但 `archive` 会拒绝 MODIFIED。缓解：归档本变更前先同步/归档 `knowledge-base-platform` 的 `knowledge-graph` delta。
- **前端无组件测试**：校验逻辑只能靠类型检查与手测。缓解：把编码规则收敛为单常量，后端保留同名正则作为最终防线。

## Migration Plan

1. 部署后端代码与迁移（两表数据量小，`ALTER TABLE` 直接执行；无新增依赖）。执行 `alembic upgrade head`，确认两表 `status` 类型为 `smallint`、默认值 `0`、`CHECK` 生效。
2. 同版本发布前端（状态值类型与后端一一对应，跨版本不兼容）。
3. 调用 `POST /api/v1/ontology/sync` 重放图库，确认 `:Class.status` 为整数。
4. 回滚：前端回滚到旧版本 + `alembic downgrade -1`（字符串列恢复，0→`active`、1→`disabled`）。注意迁移已把原停用数据抹平，降级后无法恢复历史停用状态。

## Open Questions

- 是否给 `code` 增加数据库 `CHECK`（与 API 同形态），以约束脚本直连入库？历史数据形态未审计，先保持 API 层校验；如需要，先跑一次存量扫描再决定。
