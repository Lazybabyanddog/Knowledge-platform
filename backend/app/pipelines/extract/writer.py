"""候选实体关系的落库器。

默认写 Neo4j（``status=proposed``）；单测替换成内存实现，避免图库依赖。

与旧的"整行覆盖"写法相比，这里多了两步：

1. **身份解析**：先按 ``(workspace_id, class_code, identity_key)`` 回查既有节点。
   命中就用既有节点的 id（被合并的尸体节点还会被重定向到 ``merged_into``），
   没命中才用候选行自带的默认 id。跨文档对齐、人工改名、合并复活都靠这一步。
2. **增补合并**：名称保留首次写入值，文档集合/证据集合/别名取并集，
   属性既有非空值优先，状态只在创建时置 ``proposed``（在 Cypher 里保证）。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.domain.knowledge import dedupe_relations, relation_evidence_key, relation_instance_id
from app.infra.cache.redis_client import get_redis
from app.infra.graph.entity_repository import EntityRepository

logger = logging.getLogger(__name__)

CandidateWriter = Callable[
    [str, str, str, list[dict[str, Any]], list[dict[str, Any]]],
    dict[str, int],
]

_PERSIST_LOCK_PREFIX = "graph:persist:"
_PERSIST_LOCK_TTL_SECONDS = 60
_LOCK_RETRY_INTERVAL_SECONDS = 0.05


def _now_ms() -> int:
    return int(time.time() * 1000)


def _union(*values: Any) -> list[str]:
    merged: list[str] = []
    for value in values:
        for item in value or []:
            text = str(item)
            if text and text not in merged:
                merged.append(text)
    return merged


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _merge_attributes(
    existing: dict[str, Any] | None, incoming: dict[str, Any] | None
) -> tuple[dict[str, Any], int]:
    """既有非空值优先；冲突只计数，不覆盖人工/先到的那份数据。"""
    merged = dict(existing or {})
    conflicts = 0
    for code, value in (incoming or {}).items():
        if _is_empty(value):
            continue
        current = merged.get(code)
        if _is_empty(current):
            merged[code] = value
        elif current != value:
            conflicts += 1
    return merged, conflicts


class _PersistLock:
    """工作空间级短锁：只为把"读既有节点 → 合并 → 写入"做成原子。

    Redis 不可用时降级为无锁并记一条降级计数——宁可偶发丢一次并集，
    也不能因为缓存故障写不进图。
    """

    def __init__(self, workspace_id: str) -> None:
        self.key = f"{_PERSIST_LOCK_PREFIX}{workspace_id}"
        self.token = f"{time.time_ns()}"
        self.degraded = False

    def __enter__(self) -> _PersistLock:
        deadline = time.monotonic() + max(0.0, settings.graph_persist_lock_timeout_seconds)
        try:
            client = get_redis()
        except Exception:  # noqa: BLE001
            logger.warning("persist 锁不可用，降级为无锁写入")
            self.degraded = True
            return self
        while True:
            try:
                if client.set(self.key, self.token, nx=True, ex=_PERSIST_LOCK_TTL_SECONDS):
                    return self
            except Exception:  # noqa: BLE001
                logger.warning("persist 锁获取异常，降级为无锁写入 key=%s", self.key)
                self.degraded = True
                return self
            if time.monotonic() >= deadline:
                logger.warning("persist 锁等待超时，降级写入 key=%s", self.key)
                self.degraded = True
                return self
            time.sleep(_LOCK_RETRY_INTERVAL_SECONDS)

    def __exit__(self, *exc_info: object) -> None:
        if self.degraded:
            return
        try:
            get_redis().delete(self.key)
        except Exception:  # noqa: BLE001
            logger.warning("persist 锁释放失败 key=%s", self.key)


def _resolve_entity_rows(
    repo: EntityRepository,
    workspace_id: str,
    task_id: str,
    document_id: str,
    entities: list[dict[str, Any]],
    now: int,
) -> tuple[list[dict[str, Any]], dict[str, str], int]:
    """回查既有节点并合并，返回 ``(行, 身份键→节点 id, 属性冲突数)``。"""
    class_codes = sorted({str(item["class_code"]) for item in entities})
    identity_keys = sorted({str(item["identity_key"]) for item in entities})
    resolved = repo.resolve_by_identity(workspace_id, class_codes, identity_keys)

    rows: list[dict[str, Any]] = []
    id_by_key: dict[str, str] = {}
    conflicts = 0
    for item in entities:
        class_code = str(item["class_code"])
        key = str(item["identity_key"])
        existing = resolved.get((class_code, key))
        target_id = str(item["id"])
        if existing is not None:
            target_id = str(existing["id"])
            if existing.get("status") == "merged":
                redirect = repo.resolve_merged_target(workspace_id, target_id)
                if redirect:
                    existing = repo.get_entity(workspace_id, redirect) or existing
                    target_id = redirect
        id_by_key[f"{class_code}:{key}"] = target_id

        merged_attributes, attribute_conflicts = _merge_attributes(
            (existing or {}).get("attributes"), item.get("attributes")
        )
        conflicts += attribute_conflicts
        aliases = _union((existing or {}).get("aliases"), item.get("aliases"))
        name = (existing or {}).get("name") or item["name"]
        if name != item["name"] and item["name"] not in aliases:
            aliases.append(item["name"])
        rows.append(
            {
                "id": target_id,
                "workspace_id": workspace_id,
                "class_code": class_code,
                "name": name,
                "normalized_name": (existing or {}).get("normalized_name")
                or item["normalized_name"],
                "aliases": aliases,
                "description": (existing or {}).get("description") or item.get("description"),
                "confidence": max(
                    float((existing or {}).get("confidence") or 0.0),
                    float(item.get("confidence") or 0.0),
                ),
                "extraction_task_id": (existing or {}).get("extraction_task_id") or task_id,
                "document_id": (existing or {}).get("document_id") or document_id,
                "document_ids": _union((existing or {}).get("document_ids"), [document_id]),
                "evidence_block_ids": _union(
                    (existing or {}).get("evidence_block_ids"),
                    item.get("evidence_block_ids"),
                ),
                "identity_key": (existing or {}).get("identity_key") or key,
                "identity_kind": (existing or {}).get("identity_kind")
                or item.get("identity_kind"),
                "attributes_json": json.dumps(merged_attributes, ensure_ascii=False),
                "created_at_ms": (existing or {}).get("created_at_ms") or now,
                "updated_at_ms": now,
            }
        )
    return rows, id_by_key, conflicts


def _relation_rows(
    workspace_id: str,
    task_id: str,
    document_id: str,
    relations: list[dict[str, Any]],
    id_by_key: dict[str, str],
    now: int,
) -> list[dict[str, Any]]:
    """端点换成解析后的真实节点 id，再按最终证据键重算实例 id。"""
    rows: list[dict[str, Any]] = []
    for item in relations:
        source_id = id_by_key.get(str(item.get("source_key")))
        target_id = id_by_key.get(str(item.get("target_key")))
        if not source_id or not target_id or source_id == target_id:
            continue
        evidence_key = relation_evidence_key(
            document_id,
            item.get("evidence_block_id"),
            str(item["code"]),
            source_id,
            target_id,
            item.get("evidence_text"),
        )
        rows.append(
            {
                "id": relation_instance_id(evidence_key),
                "evidence_key": evidence_key,
                "workspace_id": workspace_id,
                "code": item["code"],
                "confidence": float(item.get("confidence") or 0.0),
                "evidence_block_id": item.get("evidence_block_id"),
                "evidence_text": item.get("evidence_text"),
                "attributes_json": json.dumps(item.get("attributes") or {}, ensure_ascii=False),
                "document_id": document_id,
                "extraction_task_id": task_id,
                "created_at_ms": now,
                "updated_at_ms": now,
                "source_id": source_id,
                "target_id": target_id,
            }
        )
    # 端点解析后可能出现新的同一证据，再收敛一次，避免写入重复边。
    return dedupe_relations(rows)


def neo4j_writer(
    workspace_id: str,
    task_id: str,
    document_id: str,
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> dict[str, int]:
    repo = EntityRepository()
    now = _now_ms()
    with _PersistLock(workspace_id) as lock:
        entity_rows, id_by_key, conflicts = _resolve_entity_rows(
            repo, workspace_id, task_id, document_id, entities, now
        )
        relation_rows = _relation_rows(
            workspace_id, task_id, document_id, relations, id_by_key, now
        )
        repo.upsert_entities(entity_rows)
        repo.upsert_relations(relation_rows)
    return {
        "entities": len(entity_rows),
        "relations": len(relation_rows),
        "attribute_conflicts": conflicts,
        "persist_lock_degraded": int(lock.degraded),
    }


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
