# Proposal

## Why

切块把"定义"和"指代"切散了：原文一段里写明「江苏省苏州市盐务管理局（简称苏州盐务局）」，另一段只说"被告苏州盐务局辩称…"。片段被单独召回时，模型既不知道这个简称指谁，引用里也只留下一个用户对不上号的短名。另一方面，不同业务文档的简称/角色/关系写法差异极大（"以下简称"、"甲方"、"原告"、"子公司"），不可能全部写死在代码的正则里。

本变更在**解析阶段的索引构建中**为每份文档生成一份别名词典，并只在生成阶段注入到 prompt。它不修改片段正文、不参与向量化，因此不需要重建已有向量，引用仍可逐字回溯原文。

## What Changes

- 新增解析策略配置 `config.alias`：总开关 `enabled`、内置规则开关 `use_builtin`、自定义规则列表 `rules`。与 `config.chunk` 沿用同一套"解析 + 保存期校验 + 读取视图"范式。
- 新增语义规则模型：一条规则 = 名称 + 类型（`ALIAS` / `ROLE` / `RELATION`）+ 触发词 + 名称形态（机构后缀、书名号、停顿词、长度上限）+ 目标基数（`ONE` / `MANY` / `UNKNOWN`）+ 优先级。内置规则随代码发布，自定义规则随策略配置存储。
- 索引构建时自算文档级别名词典：在 `build_version_index` 内对全部解析块做一次规则扫描，产出条目并与 `parse_task.stats["glossary"]` 快照。不查询知识图谱，不依赖抽取任务。
- 条目带判定字段：`certainty_level`、`resolution_status`、`candidate_entities`、`evidence_block_id`，并只允许合法组合存在。
- 一对多、证据不足、规则冲突时的默认行为是**不产出确定性对照**（默认弃），禁止用"首现优先""最近实体优先"作为通用默认策略。
- 问答拼装上下文时，只对命中片段中**实际出现**的别名注入一行对照；确定性条目写成断言，多候选写成"可能指"，均标注来源为本文简称定义。单片段注入条数有上限。
- 关闭 `config.alias` 或不产生任何条目时，prompt 与上线前逐字一致。

**非目标**（本期不做，避免与二期迁移耦合）：

- 不把对照写入 `document_chunk.content`，不参与 embedding，不重建索引。
- 不做跨文档的查询扩展（需要 workspace 级可查找的别名索引，留待二期）。
- 不做知识图谱实体对齐、实体消歧、人工审核队列。
- 不做 LLM 逐块补抽、不做 parent-child 结构性上下文。

## Capabilities

### New Capabilities

- `document-glossary`: 文档级别名词典的规则配置、索引时识别与判定、快照存储，以及在问答上下文中的受限注入。

### Modified Capabilities

无。既有 `rag-query-rewrite` 的查询改写策略不变；本变更不改变其输入契约与决策口径。

## Impact

- 配置：`parse_strategy.config.alias`（JSONB，零迁移）；请求校验入口新增 `_check_alias_config`。
- 索引：`app/services/index_service.py::build_version_index` 增加一次规则扫描与 `parse_task.stats` 回写；索引失败语义不变（别名抽取异常降级为空，不阻塞索引）。
- 检索/生成：`app/services/retrieval_service.py::build_ask_messages` 与 `ChunkHit` 需要携带别名对照；`app/api/v1/retrieval.py` 的问答入口按新签名接线。
- 新模块：别名规则解析与校验、规则执行器、判定器（纯函数）。
- 数据：仅 `parse_task.stats` 新增 `glossary` 键，无 schema 变更、无数据迁移；重建索引即重算。
- 风险：规则误命中会把错误对照带进 prompt，因此"确定性"与"可能指"必须严格分流，且一对多默认弃。
