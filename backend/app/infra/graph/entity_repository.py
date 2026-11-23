"""Neo4j 实体/关系仓储。

约定：
- 节点 ``(:Entity)`` 以 ``id`` 为全局唯一键，``workspace_id`` 做空间隔离；
- 关系 ``[:REL]`` 携带 ``id``，方向即语义方向（源实体 → 目标实体）；
- 合并撤销所需的现场数据全部快照在 ``(:MergeRecord)`` 的 ``payload_json`` 里，
  这样撤销不依赖 PostgreSQL，也不需要改动实体节点的属性结构。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.infra.graph.driver import graph_session

ENTITY_LABEL = "Entity"
REL_LABEL = "REL"

# 跨文档对齐后，同一身份节点会被多个文档/任务反复写入：
# - 状态只在创建时置 proposed，已确认/已驳回绝不被降级；
# - 名称、首次来源、创建时间保留首次写入的值，新名称只会进 aliases；
# - 别名、文档集合、证据集合由 writer 在 Python 侧合并后整串写回（并集）；
# - 属性同样是合并后的整串。
_UPSERT_ENTITIES = """
UNWIND $rows AS row
MERGE (e:Entity {id: row.id})
ON CREATE SET
    e.workspace_id = row.workspace_id,
    e.class_code = row.class_code,
    e.name = row.name,
    e.normalized_name = row.normalized_name,
    e.description = row.description,
    e.status = coalesce(row.status, 'proposed'),
    e.confidence = row.confidence,
    e.extraction_task_id = row.extraction_task_id,
    e.document_id = row.document_id,
    e.identity_key = row.identity_key,
    e.identity_kind = row.identity_kind,
    e.created_at_ms = row.created_at_ms
ON MATCH SET
    e.description = coalesce(e.description, row.description),
    e.confidence = CASE WHEN row.confidence > coalesce(e.confidence, 0.0)
                        THEN row.confidence ELSE e.confidence END
SET e.aliases = row.aliases,
    e.document_ids = row.document_ids,
    e.evidence_block_ids = row.evidence_block_ids,
    e.attributes_json = row.attributes_json,
    e.updated_at_ms = row.updated_at_ms
"""

_LINK_CLASSES = """
UNWIND $rows AS row
MATCH (e:Entity {id: row.id})
MATCH (c:Class {workspace_id: row.workspace_id, code: row.class_code})
MERGE (e)-[:INSTANCE_OF]->(c)
"""

_UPSERT_RELATIONS = """
UNWIND $rows AS row
MATCH (s:Entity {id: row.source_id})
MATCH (t:Entity {id: row.target_id})
MERGE (s)-[r:REL {id: row.id}]->(t)
ON CREATE SET
    r.workspace_id = row.workspace_id,
    r.code = row.code,
    r.status = coalesce(row.status, 'proposed'),
    r.confidence = row.confidence,
    r.evidence_block_id = row.evidence_block_id,
    r.document_id = row.document_id,
    r.extraction_task_id = row.extraction_task_id,
    r.evidence_key = row.evidence_key,
    r.evidence_text = row.evidence_text,
    r.created_at_ms = row.created_at_ms
ON MATCH SET
    r.confidence = CASE WHEN row.confidence > coalesce(r.confidence, 0.0)
                        THEN row.confidence ELSE r.confidence END
SET r.attributes_json = row.attributes_json,
    r.updated_at_ms = row.updated_at_ms
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _parse_attributes(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def node_to_entity(node: dict[str, Any]) -> dict[str, Any]:
    """把 Neo4j 节点属性转成服务层使用的扁平字典。"""
    return {
        "id": node.get("id"),
        "workspace_id": node.get("workspace_id"),
        "class_code": node.get("class_code"),
        "name": node.get("name"),
        "normalized_name": node.get("normalized_name"),
        "aliases": list(node.get("aliases") or []),
        "description": node.get("description"),
        "status": node.get("status"),
        "confidence": float(node.get("confidence") or 0.0),
        "reject_reason": node.get("reject_reason"),
        "merged_into": node.get("merged_into"),
        "merge_record_id": node.get("merge_record_id"),
        "merged_at_ms": node.get("merged_at_ms"),
        "extraction_task_id": node.get("extraction_task_id"),
        "document_id": node.get("document_id"),
        "document_ids": list(node.get("document_ids") or []),
        "evidence_block_ids": list(node.get("evidence_block_ids") or []),
        "identity_key": node.get("identity_key"),
        "identity_kind": node.get("identity_kind"),
        "attributes": _parse_attributes(node.get("attributes_json")),
        "created_at_ms": node.get("created_at_ms"),
        "updated_at_ms": node.get("updated_at_ms"),
    }


def rel_to_dict(record: dict[str, Any]) -> dict[str, Any]:
    rel = record.get("rel") or record["r"]
    return {
        "id": rel.get("id"),
        "workspace_id": rel.get("workspace_id"),
        "code": rel.get("code"),
        "source_id": record.get("source_id"),
        "target_id": record.get("target_id"),
        "confidence": float(rel.get("confidence") or 0.0),
        "status": rel.get("status"),
        "evidence_block_id": rel.get("evidence_block_id"),
        "evidence_text": rel.get("evidence_text"),
        "evidence_key": rel.get("evidence_key"),
        "document_id": rel.get("document_id"),
        "extraction_task_id": rel.get("extraction_task_id"),
        "reject_reason": rel.get("reject_reason"),
        "description": rel.get("description"),
        "attributes": _parse_attributes(rel.get("attributes_json")),
        "created_at_ms": rel.get("created_at_ms"),
        "updated_at_ms": rel.get("updated_at_ms"),
    }


class EntityRepository:
    """图库侧的读写。所有方法都返回普通字典，便于服务层序列化。"""

    # ---- 写入（抽取落库） ----
    def upsert_entities(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        with graph_session() as session:
            session.run(_UPSERT_ENTITIES, rows=rows).consume()
            session.run(_LINK_CLASSES, rows=rows).consume()
        return len(rows)

    def upsert_relations(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        with graph_session() as session:
            session.run(_UPSERT_RELATIONS, rows=rows).consume()
        return len(rows)

    def resolve_by_identity(
        self,
        workspace_id: str,
        class_codes: list[str],
        identity_keys: list[str],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """按 (class_code, identity_key) 批量回查既有节点。

        写入路径先做这一步，再决定落到哪个 id：
        人工改名过的节点、被合并掉的节点都能被正确命中，而不是再长出一个新节点。
        """
        if not class_codes or not identity_keys:
            return {}
        with graph_session() as session:
            records = list(
                session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.workspace_id = $workspace_id
                      AND e.class_code IN $class_codes
                      AND e.identity_key IN $identity_keys
                    RETURN e
                    """,
                    workspace_id=workspace_id,
                    class_codes=class_codes,
                    identity_keys=identity_keys,
                )
            )
        resolved: dict[tuple[str, str], dict[str, Any]] = {}
        for item in records:
            node = node_to_entity(item["e"])
            resolved[(node["class_code"], node["identity_key"])] = node
        return resolved

    def resolve_merged_target(self, workspace_id: str, entity_id: str) -> str | None:
        """被合并节点的写入要重定向到 merged_into，避免"复活"出孤立节点。"""
        with graph_session() as session:
            record = session.run(
                """
                MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                RETURN e.merged_into AS merged_into
                """,
                id=entity_id,
                workspace_id=workspace_id,
            ).single()
        return record["merged_into"] if record and record["merged_into"] else None

    # ---- 单点读取 ----
    def get_entity(self, workspace_id: str, entity_id: str) -> dict[str, Any] | None:
        with graph_session() as session:
            record = session.run(
                "MATCH (e:Entity {id: $id, workspace_id: $workspace_id}) RETURN e",
                id=entity_id,
                workspace_id=workspace_id,
            ).single()
        return node_to_entity(record["e"]) if record else None

    def get_relation(self, workspace_id: str, relation_id: str) -> dict[str, Any] | None:
        with graph_session() as session:
            record = session.run(
                """
                MATCH (s:Entity)-[r:REL {id: $id}]->(t:Entity)
                WHERE r.workspace_id = $workspace_id
                RETURN r, s.id AS source_id, t.id AS target_id
                """,
                id=relation_id,
                workspace_id=workspace_id,
            ).single()
        return rel_to_dict(record) if record else None

    # ---- 列表 ----
    def list_entities(
        self,
        workspace_id: str,
        *,
        statuses: list[str] | None = None,
        class_code: str | None = None,
        keyword: str | None = None,
        min_confidence: float | None = None,
        document_id: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = _entity_where(
            workspace_id,
            statuses=statuses,
            class_code=class_code,
            keyword=keyword,
            min_confidence=min_confidence,
            document_id=document_id,
        )
        count_query = f"MATCH (e:Entity) WHERE {where} RETURN count(e) AS total"
        list_query = (
            f"MATCH (e:Entity) WHERE {where} RETURN e "
            "ORDER BY e.confidence DESC, e.created_at_ms DESC SKIP $skip LIMIT $limit"
        )
        params.update({"skip": skip, "limit": limit})
        with graph_session() as session:
            total = session.run(count_query, **params).single()["total"]
            records = list(session.run(list_query, **params))
        return [node_to_entity(item["e"]) for item in records], int(total)

    def list_relations(
        self,
        workspace_id: str,
        *,
        statuses: list[str] | None = None,
        code: str | None = None,
        min_confidence: float | None = None,
        document_id: str | None = None,
        extraction_task_id: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["r.workspace_id = $workspace_id"]
        params: dict[str, Any] = {"workspace_id": workspace_id}
        if statuses:
            clauses.append("r.status IN $statuses")
            params["statuses"] = statuses
        if code:
            clauses.append("r.code = $code")
            params["code"] = code
        if min_confidence is not None:
            clauses.append("r.confidence >= $min_confidence")
            params["min_confidence"] = min_confidence
        if document_id:
            clauses.append("r.document_id = $document_id")
            params["document_id"] = document_id
        if extraction_task_id:
            clauses.append("r.extraction_task_id = $extraction_task_id")
            params["extraction_task_id"] = extraction_task_id
        where = " AND ".join(clauses)
        params.update({"skip": skip, "limit": limit})
        with graph_session() as session:
            total = session.run(
                f"MATCH (:Entity)-[r:REL]->(:Entity) WHERE {where} RETURN count(r) AS total",
                **params,
            ).single()["total"]
            records = list(
                session.run(
                    f"""
                    MATCH (s:Entity)-[r:REL]->(t:Entity)
                    WHERE {where}
                    RETURN r, s.id AS source_id, t.id AS target_id
                    ORDER BY r.confidence DESC, r.created_at_ms DESC SKIP $skip LIMIT $limit
                    """,
                    **params,
                )
            )
        return [rel_to_dict(item) for item in records], int(total)

    # ---- 审核：状态流转 ----
    def update_entity(
        self,
        workspace_id: str,
        entity_id: str,
        props: dict[str, Any],
    ) -> dict[str, Any] | None:
        payload = {key: value for key, value in props.items() if value is not None}
        payload["updated_at_ms"] = _now_ms()
        with graph_session() as session:
            record = session.run(
                """
                MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                SET e += $props
                RETURN e
                """,
                id=entity_id,
                workspace_id=workspace_id,
                props=payload,
            ).single()
        return node_to_entity(record["e"]) if record else None

    def clear_reject_reason(self, workspace_id: str, entity_id: str) -> None:
        with graph_session() as session:
            session.run(
                """
                MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                REMOVE e.reject_reason, e.merged_at_ms
                """,
                id=entity_id,
                workspace_id=workspace_id,
            ).consume()

    def update_relation_status(
        self,
        workspace_id: str,
        relation_id: str,
        *,
        status: str,
        reject_reason: str | None = None,
    ) -> dict[str, Any] | None:
        with graph_session() as session:
            record = session.run(
                """
                MATCH (s:Entity)-[r:REL {id: $id}]->(t:Entity)
                WHERE r.workspace_id = $workspace_id
                SET r.status = $status,
                    r.updated_at_ms = $now,
                    r.reject_reason = $reason
                RETURN r, s.id AS source_id, t.id AS target_id
                """,
                id=relation_id,
                workspace_id=workspace_id,
                status=status,
                now=_now_ms(),
                reason=reject_reason,
            ).single()
        return rel_to_dict(record) if record else None

    def update_relation_attributes(
        self,
        workspace_id: str,
        relation_id: str,
        *,
        attributes_json: str,
        description: str | None = None,
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {"attributes_json": attributes_json, "updated_at_ms": _now_ms()}
        if description is not None:
            payload["description"] = description
        with graph_session() as session:
            record = session.run(
                """
                MATCH (s:Entity)-[r:REL {id: $id}]->(t:Entity)
                WHERE r.workspace_id = $workspace_id
                SET r += $props
                RETURN r, s.id AS source_id, t.id AS target_id
                """,
                id=relation_id,
                workspace_id=workspace_id,
                props=payload,
            ).single()
        return rel_to_dict(record) if record else None

    # ---- 存量重抽：按文档清理与人工结论快照 ----
    def detach_document(self, workspace_id: str, document_id: str) -> tuple[int, int]:
        """把某文档从实体 ``document_ids`` 里剔除，节点空了才删。

        跨文档对齐后一个节点可能同时属于多份文档，直接删节点会误伤其他文档的知识。
        返回 ``(命中的实体数, 删除的实体数)``。
        """
        with graph_session() as session:
            matched = session.run(
                """
                MATCH (e:Entity)
                WHERE e.workspace_id = $workspace_id
                  AND $document_id IN coalesce(e.document_ids, [])
                RETURN count(e) AS total
                """,
                workspace_id=workspace_id,
                document_id=document_id,
            ).single()
            touched = session.run(
                """
                MATCH (e:Entity)
                WHERE e.workspace_id = $workspace_id
                  AND $document_id IN coalesce(e.document_ids, [])
                SET e.document_ids = [item IN e.document_ids WHERE item <> $document_id],
                    e.updated_at_ms = $now
                WITH e, size(e.document_ids) AS remaining
                WHERE remaining = 0
                DETACH DELETE e
                RETURN count(*) AS removed
                """,
                workspace_id=workspace_id,
                document_id=document_id,
                now=_now_ms(),
            ).single()
        return int(matched["total"]), int(touched["removed"])

    def list_document_relations(self, workspace_id: str, document_id: str) -> list[dict[str, Any]]:
        with graph_session() as session:
            records = list(
                session.run(
                    """
                    MATCH (s:Entity)-[r:REL]->(t:Entity)
                    WHERE r.workspace_id = $workspace_id AND r.document_id = $document_id
                    RETURN r, s.id AS source_id, t.id AS target_id,
                           s.identity_key AS source_identity_key,
                           t.identity_key AS target_identity_key
                    """,
                    workspace_id=workspace_id,
                    document_id=document_id,
                )
            )
        rows = []
        for item in records:
            row = rel_to_dict(item)
            row["source_identity_key"] = item["source_identity_key"]
            row["target_identity_key"] = item["target_identity_key"]
            rows.append(row)
        return rows

    def delete_document_relations(self, workspace_id: str, document_id: str) -> int:
        with graph_session() as session:
            record = session.run(
                """
                MATCH (:Entity)-[r:REL]->(:Entity)
                WHERE r.workspace_id = $workspace_id AND r.document_id = $document_id
                WITH r LIMIT 10000
                DELETE r
                RETURN count(*) AS removed
                """,
                workspace_id=workspace_id,
                document_id=document_id,
            ).single()
        return int(record["removed"] if record else 0)

    def review_snapshot(self, workspace_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """重抽前快照人工结论，重抽后按身份回贴。"""
        with graph_session() as session:
            entity_records = list(
                session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.workspace_id = $workspace_id
                      AND e.status IN ['confirmed', 'rejected']
                    RETURN e.id AS id, e.class_code AS class_code, e.name AS name,
                           e.normalized_name AS normalized_name, e.identity_key AS identity_key,
                           e.document_ids AS document_ids,
                           e.status AS status, e.reject_reason AS reject_reason
                    """,
                    workspace_id=workspace_id,
                )
            )
            relation_records = list(
                session.run(
                    """
                    MATCH (s:Entity)-[r:REL]->(t:Entity)
                    WHERE r.workspace_id = $workspace_id
                      AND r.status IN ['confirmed', 'rejected']
                    RETURN r.id AS id, r.code AS code, r.evidence_key AS evidence_key,
                           r.document_id AS document_id,
                           s.id AS source_id, t.id AS target_id,
                           s.identity_key AS source_identity_key,
                           t.identity_key AS target_identity_key,
                           r.status AS status, r.reject_reason AS reject_reason
                    """,
                    workspace_id=workspace_id,
                )
            )
        return (
            [dict(item) for item in entity_records],
            [dict(item) for item in relation_records],
        )

    def apply_review_status(
        self,
        workspace_id: str,
        *,
        entity_id: str,
        status: str,
        reject_reason: str | None,
    ) -> None:
        with graph_session() as session:
            session.run(
                """
                MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                SET e.status = $status, e.reject_reason = $reason, e.updated_at_ms = $now
                """,
                id=entity_id,
                workspace_id=workspace_id,
                status=status,
                reason=reject_reason,
                now=_now_ms(),
            ).consume()

    def apply_relation_review_status(
        self,
        workspace_id: str,
        *,
        relation_id: str,
        status: str,
        reject_reason: str | None,
    ) -> None:
        with graph_session() as session:
            session.run(
                """
                MATCH (:Entity)-[r:REL {id: $id}]->(:Entity)
                WHERE r.workspace_id = $workspace_id
                SET r.status = $status, r.reject_reason = $reason, r.updated_at_ms = $now
                """,
                id=relation_id,
                workspace_id=workspace_id,
                status=status,
                reason=reject_reason,
                now=_now_ms(),
            ).consume()

    # ---- 合并 ----
    def merge_entities(
        self,
        workspace_id: str,
        target_id: str,
        source_ids: list[str],
    ) -> tuple[dict[str, Any] | None, str]:
        """把 source 合并进 target，返回 (合并记录, 失败原因)。"""
        now = _now_ms()
        record_id = str(uuid.uuid4())
        with graph_session() as session:
            nodes = {
                record["e"]["id"]: dict(record["e"])
                for record in session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.workspace_id = $workspace_id AND e.id IN $ids
                    RETURN e
                    """,
                    workspace_id=workspace_id,
                    ids=[target_id, *source_ids],
                )
            }
            if target_id not in nodes:
                return None, "目标实体不存在"
            if nodes[target_id].get("status") == "merged":
                return None, "目标实体已被合并"
            missing = [item for item in source_ids if item not in nodes]
            if missing:
                return None, "待合并实体不存在"
            if any(nodes[item].get("status") == "merged" for item in source_ids):
                return None, "待合并实体已被合并"

            moved = _collect_relations(session, workspace_id, source_ids, target_id)
            if moved:
                session.run(
                    "UNWIND $ids AS rid MATCH ()-[r:REL {id: rid}]->() DELETE r",
                    ids=[item["id"] for item in moved],
                ).consume()
                _recreate_relations(session, workspace_id, target_id, source_ids, moved)

            aliases = list(nodes[target_id].get("aliases") or [])
            evidence = list(nodes[target_id].get("evidence_block_ids") or [])
            documents = list(nodes[target_id].get("document_ids") or [])
            for item in source_ids:
                node = nodes[item]
                if node.get("name") and node["name"] not in aliases:
                    aliases.append(node["name"])
                for alias in node.get("aliases") or []:
                    if alias not in aliases:
                        aliases.append(alias)
                evidence.extend(node.get("evidence_block_ids") or [])
                documents.extend(node.get("document_ids") or [])
                session.run(
                    """
                    MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                    SET e.status = 'merged',
                        e.merged_into = $target_id,
                        e.merge_record_id = $record_id,
                        e.merged_at_ms = $now,
                        e.updated_at_ms = $now
                    """,
                    id=item,
                    workspace_id=workspace_id,
                    target_id=target_id,
                    record_id=record_id,
                    now=now,
                ).consume()

            session.run(
                """
                MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                SET e.aliases = $aliases,
                    e.evidence_block_ids = $evidence,
                    e.document_ids = $documents,
                    e.last_merge_id = $record_id,
                    e.updated_at_ms = $now
                """,
                id=target_id,
                workspace_id=workspace_id,
                aliases=aliases,
                evidence=sorted(set(evidence)),
                documents=sorted(set(documents)),
                record_id=record_id,
                now=now,
            ).consume()

            payload = {
                "target": {
                    "aliases": list(nodes[target_id].get("aliases") or []),
                    "evidence_block_ids": list(nodes[target_id].get("evidence_block_ids") or []),
                    "document_ids": list(nodes[target_id].get("document_ids") or []),
                    "last_merge_id": nodes[target_id].get("last_merge_id"),
                    "updated_at_ms": nodes[target_id].get("updated_at_ms"),
                },
                "sources": [
                    {
                        "id": item,
                        "status": nodes[item].get("status"),
                        "merged_into": nodes[item].get("merged_into"),
                        "merge_record_id": nodes[item].get("merge_record_id"),
                        "merged_at_ms": nodes[item].get("merged_at_ms"),
                        "updated_at_ms": nodes[item].get("updated_at_ms"),
                    }
                    for item in source_ids
                ],
                "relations": moved,
            }
            session.run(
                """
                CREATE (m:MergeRecord {
                    id: $id,
                    workspace_id: $workspace_id,
                    target_id: $target_id,
                    source_ids: $source_ids,
                    created_at_ms: $now,
                    payload_json: $payload
                })
                """,
                id=record_id,
                workspace_id=workspace_id,
                target_id=target_id,
                source_ids=source_ids,
                now=now,
                payload=json.dumps(payload, ensure_ascii=False),
            ).consume()

        return {
            "id": record_id,
            "workspace_id": workspace_id,
            "target_id": target_id,
            "source_ids": source_ids,
            "merged_count": len(source_ids),
            "created_at_ms": now,
        }, ""

    def undo_merge(
        self,
        workspace_id: str,
        target_id: str,
        *,
        merge_id: str | None,
        window_days: int,
    ) -> tuple[bool, str]:
        now = _now_ms()
        with graph_session() as session:
            record = session.run(
                """
                MATCH (m:MergeRecord)
                WHERE m.workspace_id = $workspace_id
                  AND m.target_id = $target_id
                  AND ($merge_id IS NULL OR m.id = $merge_id)
                RETURN m
                ORDER BY m.created_at_ms DESC
                LIMIT 1
                """,
                workspace_id=workspace_id,
                target_id=target_id,
                merge_id=merge_id,
            ).single()
            if record is None:
                return False, "没有可撤销的合并记录"

            merge = record["m"]
            if now - int(merge["created_at_ms"]) > window_days * 86_400_000:
                return False, "合并已超过可撤销期限"

            target = session.run(
                "MATCH (e:Entity {id: $id, workspace_id: $workspace_id}) RETURN e",
                id=target_id,
                workspace_id=workspace_id,
            ).single()
            if target is None:
                return False, "目标实体不存在"
            if int(target["e"].get("updated_at_ms") or 0) > int(merge["created_at_ms"]):
                return False, "目标实体已被修改，无法撤销"

            payload = json.loads(merge["payload_json"])
            moved = payload.get("relations") or []
            if moved:
                session.run(
                    "UNWIND $ids AS rid MATCH ()-[r:REL {id: rid}]->() DELETE r",
                    ids=[item["id"] for item in moved],
                ).consume()
                _restore_relations(session, workspace_id, moved)

            for item in payload.get("sources") or []:
                session.run(
                    """
                    MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                    SET e.status = $status,
                        e.updated_at_ms = $updated_at_ms,
                        e.merged_into = null,
                        e.merge_record_id = null,
                        e.merged_at_ms = null
                    """,
                    id=item["id"],
                    workspace_id=workspace_id,
                    status=item.get("status"),
                    updated_at_ms=item.get("updated_at_ms"),
                ).consume()

            previous = payload.get("target") or {}
            session.run(
                """
                MATCH (e:Entity {id: $id, workspace_id: $workspace_id})
                SET e.aliases = $aliases,
                    e.evidence_block_ids = $evidence,
                    e.document_ids = $documents,
                    e.updated_at_ms = $updated_at_ms
                REMOVE e.last_merge_id
                """,
                id=target_id,
                workspace_id=workspace_id,
                aliases=previous.get("aliases") or [],
                evidence=previous.get("evidence_block_ids") or [],
                documents=previous.get("document_ids") or [],
                updated_at_ms=previous.get("updated_at_ms") or now,
            ).consume()
            session.run(
                "MATCH (m:MergeRecord {id: $id}) DELETE m",
                id=merge["id"],
            ).consume()
        return True, ""

    # ---- 子图与来源 ----
    def subgraph(
        self,
        workspace_id: str,
        entity_id: str,
        *,
        depth: int,
        include_proposed: bool,
        max_nodes: int = 200,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        node_statuses = ["confirmed", "proposed"] if include_proposed else ["confirmed"]
        rel_statuses = ["confirmed", "proposed"] if include_proposed else ["confirmed"]
        with graph_session() as session:
            seed = session.run(
                "MATCH (e:Entity {id: $id, workspace_id: $workspace_id}) RETURN e",
                id=entity_id,
                workspace_id=workspace_id,
            ).single()
            if seed is None:
                return [], [], False

            nodes: dict[str, dict[str, Any]] = {entity_id: node_to_entity(seed["e"])}
            edges: dict[str, dict[str, Any]] = {}
            frontier = [entity_id]
            truncated = False

            for _ in range(depth):
                if not frontier or truncated:
                    break
                records = session.run(
                    """
                    MATCH (e:Entity)-[r:REL]->(n:Entity)
                    WHERE e.id IN $ids AND r.workspace_id = $workspace_id
                      AND r.status IN $rel_statuses AND n.status IN $node_statuses
                    RETURN r, e.id AS source_id, n.id AS target_id, n AS node
                    UNION
                    MATCH (e:Entity)<-[r:REL]-(n:Entity)
                    WHERE e.id IN $ids AND r.workspace_id = $workspace_id
                      AND r.status IN $rel_statuses AND n.status IN $node_statuses
                    RETURN r, n.id AS source_id, e.id AS target_id, n AS node
                    """,
                    ids=frontier,
                    workspace_id=workspace_id,
                    rel_statuses=rel_statuses,
                    node_statuses=node_statuses,
                )
                next_frontier: list[str] = []
                for item in records:
                    if item["target_id"] not in nodes:
                        if len(nodes) >= max_nodes:
                            truncated = True
                            continue
                        nodes[item["target_id"]] = node_to_entity(dict(item["node"]))
                        next_frontier.append(item["target_id"])
                    edges[item["r"]["id"]] = rel_to_dict(
                        {
                            "rel": item["r"],
                            "source_id": item["source_id"],
                            "target_id": item["target_id"],
                        }
                    )
                frontier = next_frontier

        return list(nodes.values()), list(edges.values()), truncated

    def count_document_legacy_entities(self, workspace_id: str, document_id: str) -> int:
        """老规则产出的实体：没有身份键或属性整串为空。

        这类节点重抽才有意义（新规则会补齐身份键与属性），也是默认范围的判定依据。
        """
        with graph_session() as session:
            record = session.run(
                """
                MATCH (e:Entity)
                WHERE e.workspace_id = $workspace_id
                  AND $document_id IN coalesce(e.document_ids, [])
                  AND (e.identity_key IS NULL
                       OR coalesce(e.attributes_json, '') IN ['', '{}'])
                RETURN count(e) AS total
                """,
                workspace_id=workspace_id,
                document_id=document_id,
            ).single()
        return int(record["total"] if record else 0)

    def list_document_entities(
        self,
        workspace_id: str,
        document_id: str,
        *,
        statuses: list[str] | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["e.workspace_id = $workspace_id", "$document_id IN e.document_ids"]
        params: dict[str, Any] = {
            "workspace_id": workspace_id,
            "document_id": document_id,
            "skip": skip,
            "limit": limit,
        }
        if statuses:
            clauses.append("e.status IN $statuses")
            params["statuses"] = statuses
        where = " AND ".join(clauses)
        with graph_session() as session:
            total = session.run(
                f"MATCH (e:Entity) WHERE {where} RETURN count(e) AS total", **params
            ).single()["total"]
            records = list(
                session.run(
                    f"""
                    MATCH (e:Entity) WHERE {where}
                    RETURN e ORDER BY e.confidence DESC, e.created_at_ms DESC
                    SKIP $skip LIMIT $limit
                    """,
                    **params,
                )
            )
        return [node_to_entity(item["e"]) for item in records], int(total)

    def delete_workspace(self, workspace_id: str) -> None:
        """测试与清库用：删除该空间的所有图数据。"""
        with graph_session() as session:
            session.run(
                "MATCH (n) WHERE n.workspace_id = $workspace_id DETACH DELETE n",
                workspace_id=workspace_id,
            ).consume()


def _entity_where(
    workspace_id: str,
    *,
    statuses: list[str] | None,
    class_code: str | None,
    keyword: str | None,
    min_confidence: float | None,
    document_id: str | None,
) -> tuple[str, dict[str, Any]]:
    clauses = ["e.workspace_id = $workspace_id"]
    params: dict[str, Any] = {"workspace_id": workspace_id}
    if statuses:
        clauses.append("e.status IN $statuses")
        params["statuses"] = statuses
    if class_code:
        clauses.append("e.class_code = $class_code")
        params["class_code"] = class_code
    if keyword:
        clauses.append(
            "(toLower(e.name) CONTAINS toLower($keyword) "
            "OR toLower(e.normalized_name) CONTAINS toLower($keyword))"
        )
        params["keyword"] = keyword
    if min_confidence is not None:
        clauses.append("e.confidence >= $min_confidence")
        params["min_confidence"] = min_confidence
    if document_id:
        clauses.append("$document_id IN e.document_ids")
        params["document_id"] = document_id
    return " AND ".join(clauses), params


def _collect_relations(
    session: Any,
    workspace_id: str,
    source_ids: list[str],
    target_id: str,
) -> list[dict[str, Any]]:
    """收集需要改挂到 target 的关系；两端都是 source 的被丢弃。"""
    moved: list[dict[str, Any]] = []
    outgoing = session.run(
        """
        MATCH (s:Entity)-[r:REL]->(o:Entity)
        WHERE r.workspace_id = $workspace_id AND s.id IN $source_ids AND o.id <> s.id
        RETURN r.id AS id, properties(r) AS props, s.id AS source_id, o.id AS target_id
        """,
        workspace_id=workspace_id,
        source_ids=source_ids,
    )
    incoming = session.run(
        """
        MATCH (o:Entity)-[r:REL]->(s:Entity)
        WHERE r.workspace_id = $workspace_id AND s.id IN $source_ids AND o.id <> s.id
        RETURN r.id AS id, properties(r) AS props, o.id AS source_id, s.id AS target_id
        """,
        workspace_id=workspace_id,
        source_ids=source_ids,
    )
    for record in [*outgoing, *incoming]:
        counterpart = (
            record["target_id"]
            if record["source_id"] in source_ids
            else record["source_id"]
        )
        if counterpart in source_ids:
            continue
        if counterpart == target_id:
            continue
        moved.append(
            {
                "id": record["id"],
                "props": dict(record["props"]),
                "source_id": record["source_id"],
                "target_id": record["target_id"],
            }
        )
    return moved


def _recreate_relations(
    session: Any,
    workspace_id: str,
    target_id: str,
    source_ids: list[str],
    moved: list[dict[str, Any]],
) -> None:
    """把以被合并实体为端点的关系改挂到 target，保持原有方向。"""
    merged = set(source_ids)
    for item in moved:
        props = {key: value for key, value in item["props"].items() if key != "id"}
        if item["source_id"] in merged:
            new_source, new_target = target_id, item["target_id"]
        elif item["target_id"] in merged:
            new_source, new_target = item["source_id"], target_id
        else:  # pragma: no cover - _collect_relations 已保证
            continue
        session.run(
            """
            MATCH (s:Entity {id: $source_id, workspace_id: $workspace_id})
            MATCH (t:Entity {id: $target_id, workspace_id: $workspace_id})
            MERGE (s)-[r:REL {id: $rel_id}]->(t)
            SET r += $props
            """,
            source_id=new_source,
            target_id=new_target,
            workspace_id=workspace_id,
            rel_id=item["id"],
            props=props,
        ).consume()


def _restore_relations(
    session: Any,
    workspace_id: str,
    moved: list[dict[str, Any]],
) -> None:
    for item in moved:
        props = {key: value for key, value in item["props"].items() if key != "id"}
        session.run(
            """
            MATCH (s:Entity {id: $source_id, workspace_id: $workspace_id})
            MATCH (t:Entity {id: $target_id, workspace_id: $workspace_id})
            MERGE (s)-[r:REL {id: $rel_id}]->(t)
            SET r += $props
            """,
            source_id=item["source_id"],
            target_id=item["target_id"],
            workspace_id=workspace_id,
            rel_id=item["id"],
            props=props,
        ).consume()


__all__ = ["EntityRepository", "node_to_entity", "rel_to_dict"]
