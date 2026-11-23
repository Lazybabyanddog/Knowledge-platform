# Proposal

## Why

管理端创建关系类型时，业务上的自然写法 `HAS_PLAINTIFF` 被 Pydantic 的编码正则拦下：`_CODE_PATTERN` 只允许 `[a-z]` 开头，接口直接返回 `40001 参数校验失败`。同一请求里的 `status` 也因为请求模型未声明该字段而被静默丢弃（`extra=ignore`），创建时只能落到数据库默认值，调用方无法在创建阶段指定有效/失效。

编码规则应收敛为"字母开头 + 字母数字下划线"，不区分大小写；`status` 应有明确的取值契约（0 有效 / 1 失效）、必传约束和数据库层面的强约束，而不是停留在字符串默认值上。

## What Changes

- 编码规则从 `^[a-z][a-z0-9_]{1,63}$` 放宽为 `^[A-Za-z][A-Za-z0-9_]{1,63}$`，实体类与关系类型共用；编码仍按原样存储、大小写敏感参与唯一性与抽取匹配，不做归一或自动大写。
- 新建实体类、新建关系类型请求新增**必传** `status`，只接受 `0`（有效）与 `1`（失效）；缺失或越界返回 400。**BREAKING**：`status` 不再接受 `"active"` / `"disabled"` 字符串。
- `PATCH` 修改接口中 `status` 保持可选，但一旦提供同样只接受 `0` / `1`；非法值返回 400。
- 实体类/关系类型响应与审计日志中的 `status` 由字符串改为整数 `0` / `1`。
- 数据库 `ontology_class.status`、`ontology_relation.status` 改为 `smallint NOT NULL DEFAULT 0` 并加 `CHECK (status IN (0, 1))`；迁移把两张表的存量数据统一置 `0`（此前被停用的定义会恢复为有效，需在发布说明中提示）。
- Neo4j `:Class` 投影写入整数状态；`POST /api/v1/ontology/sync` 重放后图库与库内一致。
- 前端本体管理页：编码输入增加大小写均可的即时校验与提示，状态下拉与停用标记改为「有效 / 失效」，创建请求显式携带 `status`。
- 列表 `include_disabled=false` 的语义不变，底层过滤条件改为 `status = 0`。

不在本次范围：抽取校验的大小写归一（`pipelines/extract/validators.py` 仍按编码精确匹配）；其他业务表的 `status` 语义与类型；`knowledge-base-platform` 变更内尚未同步的规格文本。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `knowledge-graph`: 「本体管理」需求变化——编码形态放宽为大小写均可；实体类与关系类型的状态改为必传、取值为 `0`/`1`、按整型存储，且存量数据统一视为有效。

（说明：`openspec/specs/` 目前为空，`knowledge-graph` 的基线 delta 位于在途变更 `knowledge-base-platform`；本次只修改「本体管理」这一条需求，不动该变更的其他需求，归档顺序上需先落地或同步该基线。）

## Impact

- 后端：`app/schemas/ontology.py`、`app/domain/enums.py`、`app/services/ontology_service.py`、`app/infra/db/models/ontology.py`、`app/infra/db/repositories/ontology_repository.py`、`app/infra/graph/ontology_projection.py`、新增 Alembic 迁移
- 前端：`src/api/types.ts`、`src/views/OntologyView.vue`
- 接口：`POST /api/v1/ontology/classes`、`POST /api/v1/ontology/relations` 新增必传 `status`；两类资源（实体类、关系类型）的 `status` 出参类型变化；`PATCH` 语义收紧
- 数据：两张本体表 `status` 列类型变更 + 存量数据置 0，需在迁移窗口内保证无写入（表体量小，直接 `ALTER TABLE` 即可）
- 图库：`:Class.status` 属性由字符串变为整数，未重放的历史节点在下次写入或 `/ontology/sync` 后一致
- 测试：`backend/tests/integration/test_ontology.py`、`backend/tests/integration/test_extraction.py` 需同步新的请求契约与断言
