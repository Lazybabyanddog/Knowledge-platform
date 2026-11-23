# Tasks

## 1. 数据模型与迁移

- [x] 1.1 给 `OntologyRelation` 加 `attributes` JSONB 列、给 `OntologyClass` 加 `identity_keys` JSONB 列，并新增 `ExtractionBackfillBatch` 模型；验证：`python -c` 导入模型后 `__table__` 中能看到新列。
- [x] 1.2 编写 alembic revision 接在 `d5b2f8e13c41` 之后（加两列 + 建 `extraction_backfill_batch`），并验证 `alembic upgrade head` 与 `alembic downgrade -1` 双向可执行。
- [x] 1.3 在 `schema.cypher` 增加 `Entity(workspace_id, class_code, identity_key)` 复合索引、`Entity(identity_key)` 与 `REL(evidence_key)` 索引；验证：重放两次初始化脚本不报错，`SHOW INDEXES` 能看到新索引。
- [x] 1.4 更新 `docs/db/schema.sql` 中本体相关的列注释与新表 DDL；验证：文档中的字段与迁移脚本一致（逐列核对）。

## 2. 属性定义契约

- [x] 2.1 在 `app/schemas/ontology.py` 定义 `AttributeDef`（含类型枚举、枚举值/数组元素类型互斥校验、示例数量上限）并替换 `attributes: dict[str, Any]`；验证：类型为 `text`、`enum` 缺取值、`array` 缺元素类型三种请求均返回 400。
- [x] 2.2 实现旧格式属性的读取升级（`{"type","description"}` 补默认值），并在类列表/详情返回新结构；验证：单元测试用旧格式行读出的 `name/required/examples/hint` 为默认值。
- [x] 2.3 实现标识键校验与删除保护：`identity_keys` 引用未定义属性返回 400，移除被引用属性时返回 400 并指出编码；验证：两个 400 用例的响应消息包含具体编码。
- [x] 2.4 让关系类型请求/响应支持 `attributes`，与实体类共用 `AttributeDef`；验证：创建带两个属性的关系类型后再次 GET，属性完整返回。

## 3. 提示词投影与抽取输出契约

- [x] 3.1 新增 `prompt_projection()` 生成提示词用的本体结构（去掉管理与版本字段，保留属性描述/规则/示例/枚举），替换 `ontology_payload()` 直接序列化；验证：单元测试断言投影结果中不含 `version`、`status`，且含 `hint`。
- [x] 3.2 更新 `ENTITY_SYSTEM`：输出结构加入实体 `attributes`、关系 `attributes` 与 `evidence_text`，并加入"只输出已定义属性编码""标识属性必须原样抄写"两条约束；验证：提示词快照测试比对关键片段。
- [x] 3.3 规则抽取器（无模型 key 的退化实现）同步支持属性与证据原文，避免两条路径结构不一致；验证：规则抽取器的单测覆盖带属性实体与带原文关系。

## 4. 校验与属性强转

- [x] 4.1 实现按 `AttributeDef` 的属性强转与过滤（未知编码/类型不符/枚举不符/数组项不合规一律丢字段），并在 `repair_extraction` 产出中保留 `attributes`；验证：针对六种类型的用例，合法值保留、非法值丢弃、实体保留。
- [x] 4.2 新增 `attr_unknown`、`attr_type_mismatch`、`attr_enum_mismatch`、`attr_required_missing` 四个非阻塞 issue 码并计入任务统计；验证：断言 issue 列表与统计计数，且实体未被丢弃。
- [x] 4.3 实现关系 `evidence_text` 校验（归一后需为所属内容块正文子串），不匹配时丢弃原文并记 `evidence_text_mismatch`；验证：构造越界原文用例，关系仍保留且证据原文为空。
- [x] 4.4 补充 `date`/`boolean` 解析的边界用例（`2020/1/1`、`2020年1月1日`、`是/否`、非法字符串）；验证：对应单测全部通过。

## 5. 实体身份与跨文档对齐

- [x] 5.1 在 `app/domain/knowledge.py` 用 `compute_identity()` 替换 `entity_node_id()`：支持 `identity`/`name` 两种身份、`normalize_identifier()`、标识属性值需出现在证据文本；验证：单测覆盖"键齐全 / 键缺失 / 值不在原文 → 回退名称身份"三种情形。
- [x] 5.2 让 `dedupe_entities` 按 `identity_key` 合并（而非仅归一名称），别名/证据/属性取并集，属性冲突保留既有值；验证：同名不同标识的两条候选合并后仍是两条，同标识不同名的合并为一条。
- [x] 5.3 实现写入前身份解析：按 `(workspace_id, class_code, identity_key)` 批量查询既有节点，命中则复用其 id 并跟随 `merged_into`；验证：单测用内存仓储模拟"已存在节点"与"已合并节点"两种情况，断言写入 id 为目标节点 id。
- [x] 5.4 改造实体 upsert 为增补语义（`status` 仅创建时写 `proposed`，`name` 保留首次值，`document_ids`/`evidence_block_ids`/`aliases` 并集，属性合并后回写）；验证：对已确认节点重放同身份候选后状态仍为 `confirmed`，`document_ids` 增加一项。
- [x] 5.5 给 `persist_candidates` 加工作空间级 Redis 短锁，拿不到锁时有限等待并记降级计数；验证：并发执行两个模拟写入，`document_ids` 不丢项，统计中有降级记录。
- [x] 5.6 在实体响应与查询中暴露 `identity_key` 与 `identity_kind`（供审核页识别名称回退）；验证：`GET /api/v1/knowledge/entities/{id}` 返回这两个字段。
- [x] 5.7 人工改名时把 `identity_key` 更新为新名称对应的 `name` 身份键；验证：改名后按新名称抽取同一实体不产生新节点。

## 6. 关系多实例与证据键

- [x] 6.1 在 `app/domain/knowledge.py` 实现 `relation_instance_id()`：由 `(document_id, block_id, code, source_id, target_id, normalized_evidence_text)` 生成证据键与关系 id；验证：同参数两次生成相同 id，改内容块或改端点则不同。
- [x] 6.2 `dedupe_relations` 改为按关系 id 去重并取较高置信度，移除"每对端点每种类型只留一条"的行为；验证：同端点同类型不同证据的两条关系都被保留。
- [x] 6.3 `commit_batch`/`dedupe_candidates` 透传关系 `attributes` 与 `evidence_text`，并在 id 生成处改用证据键；验证：单测断言关系行包含 `attributes`、`evidence_text`、`evidence_key`。
- [x] 6.4 `_UPSERT_RELATIONS` 写入 `attributes_json`/`evidence_text`/`evidence_key`，`rel_to_dict` 反序列化属性；验证：写入后读取关系详情能看到属性对象而非字符串。
- [x] 6.5 关系响应模型补 `attributes`、`evidence_text`、`evidence_key`；验证：`GET /api/v1/knowledge/relations/{id}` 返回这三个字段。
- [x] 6.6 新增 `PATCH /api/v1/knowledge/relations/{relation_id}` 只允许改属性与描述，`rejected` 返回 409，并写审计日志；验证：改属性成功、改已驳回关系返回 409、审计表出现记录。

## 7. 本体配置界面

- [x] 7.1 抽取 `components/AttributeEditor.vue`：表格模式支持增删改与上下移动，字段覆盖编码/展示名/类型/描述/抽取规则/示例/必填/枚举值/数组元素类型；验证：组件单测或手工核对新增一行后 `v-model` 输出含全部字段。
- [x] 7.2 在 `AttributeEditor` 内实现 JSON 高级模式双向同步与整表校验（编码重复/格式非法、枚举缺取值、数组缺元素类型、空描述），非法时阻止保存并保留输入；验证：非法 JSON 时不触发保存事件，枚举留空时行内报错。
- [x] 7.3 `OntologyView.vue` 实体类与关系类型弹窗接入该组件，列表列改为属性数量与标识键摘要；验证：页面新增属性后保存成功，列表不再出现属性 JSON 文本。
- [x] 7.4 实体类弹窗增加标识键配置（多选已有属性、可排序）；验证：接入后保存的 `identity_keys` 顺序与界面一致。
- [x] 7.5 `api/types.ts` 落成 `AttributeDef` 等类型并收紧 `api/ontology.ts` 请求体，`npm run build`/类型检查通过；验证：类型检查无 `any` 逃逸到属性定义。
- [x] 7.6 `ReviewView.vue` 关系行展示属性并接入属性编辑；验证：修改关系属性后刷新页面值仍为新值。

## 8. 存量重抽

- [x] 8.1 实现 `BackfillService`：范围选择（文档/实体类/本体版本落后）、预演模式、批次落库；验证：预演模式执行后抽取任务与图数据数量不变，批次表新增一条 `dry_run` 记录。
- [x] 8.2 实现清理步骤：实体从 `document_ids` 剔除目标文档、为空才删节点；关系按 `document_id` 删除；验证：单测覆盖"节点属于两份文档时只剔除一份、节点保留"。
- [x] 8.3 实现结论快照与回贴：按 `identity_key` → 退化为 `normalized_name` 匹配实体，按证据键匹配关系，沿用确认/驳回状态与理由；验证：已确认实体重抽后仍为已确认，已驳回关系保留理由。
- [x] 8.4 实现报告（处理/跳过/失败文档、任务数、实体与关系数、沿用计数、未沿用清单）并提供批次查询接口；验证：`GET /api/v1/extraction/backfill/{batch_id}` 返回报告字段完整。
- [x] 8.5 提供 CLI 入口 `python -m scripts.backfill_extraction`，支持 `--workspace`、`--documents`、`--dry-run`、`--force`；验证：`--help` 输出全部参数，对同一批文档连续执行两次第二次全部跳过。

## 9. 集成验证

- [x] 9.1 端到端脚本：两份文档各抽取出同一统一社会信用代码的企业，验证图里只有一个实体且 `document_ids` 含两份文档。
- [x] 9.2 端到端脚本：同一人的两段任职记载产生两条带各自起止时间的关系；重跑同一文档后关系数量不变。
- [x] 9.3 端到端脚本：带属性实体从抽取到审核页展示属性、人工修正属性、确认后状态与属性均正确。
- [x] 9.4 更新受影响的既有测试（抽取流水线、去重、校验、本体 API、知识 API）到新身份函数与属性结构；验证：`pytest` 全量通过。

