"""候选实体关系的落库器。

默认写 Neo4j（``status=proposed``）；单测替换成内存实现，避免图库依赖。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from app.infra.graph.entity_repository import EntityRepository

CandidateWriter = Callable[
    [str, str, str, list[dict[str, Any]], list[dict[str, Any]]],
    dict[str, int],
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def neo4j_writer(
    workspace_id: str,
    task_id: str,
    document_id: str,
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> dict[str, int]:
    repo = EntityRepository()
    now = _now_ms()
    entity_rows = [
        {
            "id": item["id"],
            "workspace_id": workspace_id,
            "class_code": item["class_code"],
            "name": item["name"],
            "normalized_name": item["normalized_name"],
            "aliases": item.get("aliases") or [],
            "description": item.get("description"),
            "status": "proposed",
            "confidence": float(item.get("confidence") or 0.0),
            "extraction_task_id": task_id,
            "document_id": document_id,
            "document_ids": [document_id],
            "evidence_block_ids": item.get("evidence_block_ids") or [],
            "attributes_json": json.dumps(item.get("attributes") or {}, ensure_ascii=False),
            "created_at_ms": now,
            "updated_at_ms": now,
        }
        for item in entities
    ]
    relation_rows = [
        {
            "id": item["id"],
            "workspace_id": workspace_id,
            "code": item["code"],
            "status": "proposed",
            "confidence": float(item.get("confidence") or 0.0),
            "evidence_block_id": item.get("evidence_block_id"),
            "document_id": document_id,
            "extraction_task_id": task_id,
            "created_at_ms": now,
            "updated_at_ms": now,
            "source_id": item["source_id"],
            "target_id": item["target_id"],
        }
        for item in relations
    ]
    repo.upsert_entities(entity_rows)
    repo.upsert_relations(relation_rows)
    return {"entities": len(entity_rows), "relations": len(relation_rows)}


_WRITER: CandidateWriter = neo4j_writer


def get_candidate_writer() -> CandidateWriter:
    return _WRITER


def set_candidate_writer(writer: CandidateWriter | None) -> None:
    global _WRITER
    _WRITER = writer or neo4j_writer


__all__ = [
    "CandidateWriter",
    "get_candidate_writer",
    "neo4j_writer",
    "set_candidate_writer",
]
