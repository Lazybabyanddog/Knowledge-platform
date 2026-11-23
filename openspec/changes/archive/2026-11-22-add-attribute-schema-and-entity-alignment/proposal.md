# Proposal

## Why

本体只能通过一个自由格式的 JSON 文本框描述属性，后端只校验属性个数上限，因此属性既无法被正确注入抽取提示词，也没有任何"取什么值、怎么判"的说明可给到模型；同时关系的属性完全没有承载位置。更关键的是抽取链路上属性是断的：模型输出结构里没有 `attributes` 字段，修复环节也会丢掉属性，导致实体实例的 `attributes_json` 永远为空——属性定义了也用不上。

实体身份同样与业务脱节：实体 id 由 `uuid5(抽取任务, 类编码, 归一名称)` 决定，同名不同人会被并成一个，不同文档里的同一家公司则永远是两个孤立节点，只能靠人工逐个合并。关系侧则相反，同一对实体同类型只保留置信度最高的一条，一段"2020 年任职"和一段"2015 年任职"会互相覆盖。

## What Changes

- 属性定义结构化：`attributes` 从"注释约定"升级为受校验的属性定义集合，每项包含编码、展示名、类型（含枚举/数组）、**描述**、**抽取规则**、示例、是否必填。
- 本体管理页提供**可视化属性配置**（行内增删改、上下移动），并保留 JSON 高级编辑模式，两者编辑同一份数据。
- **关系类型新增属性定义**，与实体类共用同一套属性结构与编辑器。
- 属性值全链路回流：抽取提示词要求输出属性和关系的属性值，校验环节按类型/枚举强转并丢弃非法值，去重合并与写入保留属性。
- 实体唯一标识规则：实体类可配置**有序标识键**（引用属性编码）；有完整标识值时按标识值判定同一实体，否则回退到归一名称并标记为回退。
- **BREAKING** 实体身份从"任务内名称"改为"工作空间内业务身份"，同一标识值的实体在不同文档、不同抽取任务间自动对齐为同一节点；写入时不再覆盖已有节点的状态、文档集合、属性。
- **BREAKING** 关系实例身份改为证据键 `hash(document_id, chunk_id, 关系编码, 源实体 id, 目标实体 id, 归一化证据原文)`，同一对实体之间允许存在多条同类型关系，仅在同一证据上幂等去重。
- 标识键引用的属性禁止删除，删除请求返回 400。
- 提供存量重抽：对属性为空的历史抽取结果按新本体重新抽取并重建实体身份，同时把人工确认/驳回结果按身份或名称沿用。

## Capabilities

### New Capabilities

- `extraction-backfill`: 存量抽取结果的回溯重抽与实体身份重建，包括选择范围、人工审阅状态沿用、幂等与报告。

### Modified Capabilities

- `knowledge-graph`: 本体管理（属性定义、关系属性、标识键与删除保护）、知识抽取任务（属性回流、关系证据原文）、抽取结果作为候选知识（跨文档对齐、写入不覆盖已有人工结论）、图谱关系（多实例关系与按证据去重）。

## Impact

- 数据库：`ontology_class.attributes` 语义升级（需数据规范化迁移，无需改列类型）；`ontology_relation` 新增 `attributes`；`ontology_class` 新增标识键存储。新迁移接在 `d5b2f8e13c41` 之后。
- 后端：`app/schemas/ontology.py`、`app/services/ontology_service.py`、`app/infra/db/models/ontology.py`、`app/infra/llm/prompts.py`、`app/pipelines/extract/{extractor,validators,nodes,writer}.py`、`app/domain/knowledge.py`、`app/infra/graph/{schema.cypher,entity_repository.py}`、`app/services/knowledge_service.py`、`app/schemas/knowledge.py`。
- 前端：`views/OntologyView.vue`（抽离属性编辑器组件）、`views/ReviewView.vue`（关系属性展示与编辑）、`api/{ontology,knowledge,types}.ts`。
- 兼容性：实体与关系的图侧 id 语义变更，旧节点需要重抽迁移；新旧本体定义在读取时兼容（旧 `{type, description}` 自动补默认值）。
- 测试：`backend/tests` 中抽取流水线、去重、校验、本体 API 的断言需按新身份函数与属性结构更新。
