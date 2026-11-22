// Neo4j 约束与索引初始化。
// 幂等：全部使用 IF NOT EXISTS，可重复执行。
// 与 design.md「数据模型（Neo4j）」一一对应。

// ---- 唯一性约束 ----
// 实体 id 由 PostgreSQL 生成后投影而来，是图侧主键
CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE e.id IS UNIQUE;

// 本体类按 workspace + code 唯一，支持多空间各自维护本体
CREATE CONSTRAINT class_code_unique IF NOT EXISTS
FOR (c:Class) REQUIRE (c.workspace_id, c.code) IS UNIQUE;

// 关系边同样携带业务 id，保证重放幂等
CREATE CONSTRAINT rel_id_unique IF NOT EXISTS
FOR ()-[r:REL]-() REQUIRE r.id IS UNIQUE;

// 合并记录：撤销时需要按目标实体回查最近一次合并
CREATE CONSTRAINT merge_record_id_unique IF NOT EXISTS
FOR (m:MergeRecord) REQUIRE m.id IS UNIQUE;

// ---- 索引 ----
// 归一化名称：候选去重与合并判定的主要查询入口
CREATE INDEX entity_normalized_name IF NOT EXISTS
FOR (e:Entity) ON (e.normalized_name);

// 审核页按状态筛选
CREATE INDEX entity_status IF NOT EXISTS
FOR (e:Entity) ON (e.status);

// 多空间隔离与归属统计
CREATE INDEX entity_workspace IF NOT EXISTS
FOR (e:Entity) ON (e.workspace_id);

// 按本体类过滤实体
CREATE INDEX entity_class_code IF NOT EXISTS
FOR (e:Entity) ON (e.class_code);

// 候选关系筛选
CREATE INDEX rel_status IF NOT EXISTS
FOR ()-[r:REL]-() ON (r.status);

// 合并记录按目标实体回查
CREATE INDEX merge_record_target IF NOT EXISTS
FOR (m:MergeRecord) ON (m.target_id);
