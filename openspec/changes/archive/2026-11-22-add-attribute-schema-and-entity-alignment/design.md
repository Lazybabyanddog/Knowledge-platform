# Design

## Context

见 `proposal.md`。与实现相关的现状约束：

- 本体在 PostgreSQL（`ontology_class.attributes` 为 `JSONB` 且无结构校验；`ontology_relation` 完全没有属性列），实例在 Neo4j（`(:Entity)`/`[:REL]` 以 `id` 为唯一键），本体经 `/api/v1/ontology/sync` 单向投影。
- 流水线：`chunk_blocks -> classify_blocks -> extract_entities -> extract_relations -> schema_validate -> repair_output|skip_batch -> commit_batch -> dedupe_candidates -> persist_candidates`，已按批次 checkpoint 支持重试续跑。
- `ontology_payload()` 把 ORM 行整体塞进提示词，但 `ENTITY_SYSTEM` 输出结构里没有 `attributes`，`repair_extraction()` 重建 dict 时也不带它，因此实体属性恒为空。
- 实体 id = `uuid5(task_id, class_code, normalize_name(name))`；关系 id = `uuid5(task_id, source, code, target)`，`dedupe_relations` 按 `(source,target,code)` 只留最高置信度。
- 人工结论存在 `status`（`proposed/confirmed/rejected/merged`），合并靠 `merge_entities` + `MergeRecord`（7 天可撤销）。Redis 已在用，可用于短临界区互斥。

## Goals / Non-Goals

**Goals:**

- 属性从"约定"变为"契约"：可可视化编辑、可校验、可注入提示词，并能回流到实例。
- 实体身份从"任务内名称"改为"工作空间内业务身份"，且身份解析成为写入路径上的显式步骤。
- 关系成为可携带属性的多实例对象，去重以证据为粒度。
- 存量数据可重抽迁移，人工结论尽量不丢。

**Non-Goals:**

- 不做关系节点化（reification），关系仍是一条 `[:REL]`。
- 不做模糊/向量级对齐：只认标识键与归一名称两种确定性规则。
- 不做属性级版本管理与属性编码重命名迁移。
- 不给实体类/关系类新增 `extraction_hint` 字段（只做在属性层），类/关系的 `description` 继续承担判定口径。
- 不在前端提供常规"选中文档一键重抽"入口，重抽只做运维入口。

## Decisions

### D1. 属性定义仍存 JSONB，但用 Pydantic 模型变成契约

`AttributeDef` 是唯一结构定义，读写都过它，列类型不变。

```text
AttributeDef
  code        ^[A-Za-z][A-Za-z0-9_]{1,63}$，同类内唯一
  name        <=64       展示名，同时进提示词
  type        string | number | date | boolean | enum | array
  description 1..1000    抽取语义，必填
  hint        <=500      抽取规则/口径，可选
  examples    <=3 条，每条 <=200
  required    bool=False
  enum_values enum 时必填且非空
  items_type  array 时必填，不允许再为 array
```

- 属性总数上限沿用 30。
- **旧格式兼容**：读到 `{"type","description"}` 时补默认值（`name=code`、`required=False`、`examples=[]`、`hint=None`）后按新结构返回，**不做数据改写**；保存时整体写回新结构。
- 备选：拆子表。放弃——本体是整体版本化的，payload 每次整体序列化，子表只增加 join 与排序成本；且属性重命名已列为非目标。

### D2. 关系类型与实体类共用同一套属性定义

`ontology_relation.attributes JSONB NOT NULL DEFAULT '{}'`，复用 `AttributeDef` 与同一个前端编辑器组件。

### D3. 标识键存在实体类上，是有序属性编码列表

`ontology_class.identity_keys JSONB NOT NULL DEFAULT '[]'`（如 `["uscc"]`、`["name","birth_date"]`）。

- 顺序即拼接顺序；元素必须是本类已定义的属性编码，否则 400。
- 用 JSONB 而非 `ARRAY(Text)`：总是与类定义整体读出、从不按元素查询。
- **删除保护**：提交后若某编码仍被 `identity_keys` 引用却被从 `attributes` 移除，返回 400 并指出该编码；错误信息提示"先移除标识键再删属性"。

### D4. 实体身份是显式解析步骤，不是 id 算式的副作用

```text
identity_kind  = "identity" | "name"
identity_value = identity ? join(normalize_identifier(各标识属性值), "\x1f")
                          : normalize_name(name)
identity_key   = f"{identity_kind}:{identity_value}"
default_id     = uuid5(ENTITY_NAMESPACE, f"{workspace_id}:{class_code}:{identity_key}")
```

判定规则：

1. 标识键为空 → `name` 身份。
2. 标识键非空但任一标识属性值为空 → `name` 身份并标记回退。
3. 标识属性值**必须在该实体的证据文本中找到**（归一后子串匹配），否则按缺失处理并回退。这条护栏防的是模型幻觉出一个证号把两个人并成一个人。
4. 其余 → `identity` 身份。

写入前先解析目标节点（与现状最大的差别）：

```text
for each candidate:
    kind, value = compute_identity(class_def, attributes, name, evidence_texts)
    existing = lookup(workspace_id, class_code, identity_key)
    target_id = existing ? follow_merged(existing) : uuid5(...)
    merge attributes / aliases / document_ids / evidence in python
upsert by target_id
```

- 解析按批次一次查询（`WHERE workspace_id=$ws AND class_code IN $codes AND identity_key IN $keys`），依赖新索引。
- 该步骤同时解决两件事：人工改名后按新名称命中旧节点；被合并的实体再次被抽到时重定向到 `merged_into` 而不是长出孤立节点。
- 备选：保留 id 算式、跨文档靠事后图扫描匹配。放弃——需要额外扫描，且无法解决合并复活。

### D5. 跨文档写入语义：增补而非覆盖

| 字段 | 语义 |
|---|---|
| `status` | 仅 `ON CREATE` 写 `proposed`，已存在节点状态不动 |
| `name` / `normalized_name` | 保留首次写入值，新名称进 `aliases` |
| `document_ids` / `evidence_block_ids` / `aliases` | 取并集 |
| `document_id` / `extraction_task_id` | 保留首次来源 |
| `attributes_json` | Python 合并后整串写回：既有非空值优先，冲突计数进任务统计 |

并发：同一工作空间可能有多个抽取任务同时写同一身份节点。`persist_candidates` 用 Redis 短锁（`graph:persist:{workspace_id}`，带超时）串行化"读既有节点 → 合并 → 写入"；拿不到锁时有限等待，超时则写入并记降级计数。

### D6. 关系实例身份 = 证据键

```text
evidence_key = json.dumps([document_id, block_id, code, source_id, target_id, normalized_evidence_text])
REL.id       = uuid5(RELATION_NAMESPACE, evidence_key)
```

- `normalized_evidence_text` 复用 `normalize_name()`（NFKC + 去标点空白 + 小写）。
- 证据原文来自模型新增的 `evidence_text`；校验要求它出现在该内容块正文中（归一后子串）。不匹配则丢弃原文并记 `evidence_text_mismatch`，此时证据键退化为"同内容块、同端点、同类型只有一条"。
- `dedupe_relations` 的 key 从 `(source,target,code)` 改为 `id`，仍取较高置信度。
- `[:REL]` 新增 `attributes_json`、`evidence_text`、`evidence_key`；属性以 JSON 字符串存储（Neo4j 关系属性不支持嵌套 map，与实体一致）。
- 备选：用"关系判别键"（起止时间等）定义身份。放弃——要求模型把属性抽得逐字一致才幂等；证据键在重跑时天然稳定，且贴合"一条关系对应一处原文记载"。

### D7. 提示词投影与输出契约

新增 `prompt_projection()`，让提示词结构与数据库结构解耦：

```text
classes:   [{code, name, description, attributes: [{code, name, type, required, description, hint, examples, enum_values}]}]
relations: [{code, name, description, source_class_code, target_class_code, attributes: [...]}]
```

- `id/version/status/created_*` 等管理字段不进提示词。
- 输出契约新增：实体 `attributes`、关系 `attributes` 与 `evidence_text`。
- 系统提示增加两条硬约束：只输出本体已定义的属性编码；标识属性在原文出现时必须原样抄写。
- 体积增长由投影裁剪 + 现有 `class_codes` 限缩共同缓解。

### D8. 校验策略：丢字段、不丢对象

新增非阻塞 issue 码：`attr_unknown`、`attr_type_mismatch`、`attr_enum_mismatch`、`attr_required_missing`、`evidence_text_mismatch`。

| 类型 | 接受 | 规范化 |
|---|---|---|
| `string` | 字符串 | strip，空串视为未给出 |
| `number` | 数字或数字字符串 | 转 `int`/`float` |
| `date` | `YYYY-MM-DD`、`YYYY/MM/DD`、`YYYY年M月D日` | 统一为 `YYYY-MM-DD` |
| `boolean` | 布尔或 `"true"/"false"/"是"/"否"` | 转布尔 |
| `enum` | 字符串 | 大小写不敏感匹配，落库用声明时的原值 |
| `array` | 列表 | 逐项按 `items_type` 强转，丢弃不合规项 |

`required` 只告警，不影响入库——召回优先于字段完整性。

### D9. 关系属性的人工修正入口

新增 `PATCH /api/v1/knowledge/relations/{relation_id}`，只允许改 `attributes`（可选 `description`）；`rejected` 返回 409。审核页关系行可展开编辑属性，校验以服务端 `AttributeDef` 为准。

### D10. 存量重抽：先快照结论、再按文档重建、最后回贴

```text
[1] 选范围   按文档 / 实体类 / 本体版本落后 的抽取任务
[2] 快照结论 entity: (class_code, identity_key|normalized_name, status, reject_reason)
             relation: (code, 端点 identity_key, evidence_key, status, reject_reason)
[3] 清理     实体：从 document_ids 剔除目标文档，为空才删节点
             关系：document_id ∈ 目标集合的直接删
[4] 重抽     每份文档按当前本体重新触发，任务 config 记 batch_id
[5] 回贴     按 identity_key 匹配 → 退化为 normalized_name → 应用状态
[6] 报告     写入 extraction_backfill_batch.report
```

- **只剔除文档而不是整节点删除**：跨文档对齐后一个节点可能属于多份文档，删节点会误伤其他文档。
- 幂等：`scope` 记录已处理的 `(document_id, document_version_id)`，重复执行跳过，`--force` 覆盖。
- 批次用单表 `extraction_backfill_batch`（`scope/report` 为 JSONB），不建子表；报告只留汇总与未沿用清单。
- 单文档失败记 `failed` + 错误码后继续。
- 备选：只做图内 id 重映射不重抽。放弃——属性本就是空的，重映射拿不到属性值，也修不掉关系实例语义。

### D11. 前端把属性编辑器抽成独立组件

- 新增 `components/AttributeEditor.vue`：`v-model` 绑定 `AttributeDef[]`，表格模式负责增删改与上移/下移，右上角切换 JSON 高级模式；两种模式编辑同一份数据，JSON 非法时不覆盖已输入内容，保存前整表校验（枚举缺取值、数组缺元素类型、编码重复/格式非法）。
- `OntologyView.vue` 的实体类与关系类型弹窗共用该组件；列表列改为"属性数量 + 标识键摘要"。
- `ReviewView.vue` 关系行展开显示属性，接入 `PATCH /knowledge/relations/{id}`。
- 属性定义新结构在 `api/types.ts` 里落成 `AttributeDef` 接口，`api/ontology.ts` 去掉 `Record<string, unknown>` 的松散请求体。

## Risks / Trade-offs

- [误合并：模型给出错误标识值] → 标识属性值必须在证据原文中出现才参与构键，否则回退名称身份；已确认实体不被自动改写状态。
- [并发写同一身份节点丢更新] → `persist_candidates` 用 Redis 工作空间级短锁；拿不到锁记降级计数。
- [关系数量增长（每条证据一条边）] → 审核页与子图查询按 `(端点, 类型)` 分组展示，展开看实例；单文档关系数超阈值时在统计里给出提示。
- [重抽期间新旧节点并存] → 建议维护窗口执行；脚本按文档幂等，可中断续跑。
- [提示词体积与成本上升] → 投影裁剪、任务按 `class_codes` 限缩；属性上限 30。
- [date 解析歧义（如 `2020.1.1`）] → 只接受列出的三种形态，其余丢弃并记 `attr_type_mismatch`。
- [旧测试大量依赖 id 算式] → 身份函数与 `entity_node_id` 一起替换，测试断言改为按 `identity_key` 与解析结果校验。

## Migration Plan

1. Alembic 新 revision 接在 `d5b2f8e13c41` 之后：`ontology_relation` 加 `attributes`；`ontology_class` 加 `identity_keys`；新建 `extraction_backfill_batch`。仅加列/加表，不改列类型，可安全先迁移。
2. Neo4j：`schema.cypher` 增加 `Entity(workspace_id, class_code, identity_key)` 复合索引、`Entity(identity_key)` 索引、`REL(evidence_key)` 索引；启动时幂等重放。
3. 部署后端（新身份函数、属性回流、证据键）。
4. 执行 `python -m scripts.backfill_extraction --workspace <id> --dry-run` 核对范围，再去掉 `--dry-run` 正式执行。
5. 回滚策略：代码回滚 + 迁移 `down`（drop 列/表）。图侧不可平滑回滚，回滚窗口内不执行重抽，或以 Neo4j 备份恢复。

## Open Questions

- 子图查询是否要把同 `(端点, 类型)` 的多条关系合并成一条并返回实例列表？本期先按原样返回多条，聚合只做在审核页。

