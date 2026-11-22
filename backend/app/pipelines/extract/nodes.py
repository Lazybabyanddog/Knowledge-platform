"""抽取图节点。

流程：分块 → 分类 → 抽实体 → 抽关系 → 校验 →（修复 ⇄ 校验）→ 提交/跳过 → 去重 → 落库。
每个节点只改自己关心的字段，路由由 ``route_*`` 读取 ``state["route"]``。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.core.config import settings
from app.domain.knowledge import (
    dedupe_entities,
    dedupe_relations,
    entity_node_id,
    normalize_name,
)
from app.pipelines.extract.extractor import get_extractor
from app.pipelines.extract.state import ExtractState
from app.pipelines.extract.validators import (
    issues_to_dicts,
    repair_extraction,
    validate_extraction,
)
from app.pipelines.extract.writer import get_candidate_writer

logger = logging.getLogger(__name__)

RELATION_NAMESPACE = uuid.UUID("1c6e0d38-0f6c-5b8a-9f52-6a2f6a7f4d10")
_SKIPPABLE_TYPES = {"footer", "header", "toc"}
_MIN_CONTENT = 4


def chunk_blocks(state: ExtractState) -> dict[str, Any]:
    blocks = [item for item in state.get("blocks") or [] if str(item.get("content") or "").strip()]
    size = int(state.get("batch_size") or settings.extract_batch_size)
    batches = [blocks[start : start + size] for start in range(0, len(blocks), size)]
    return {
        "batches": batches,
        "batch_index": 0,
        "entities": [],
        "relation_rows": [],
        "skipped_block_ids": [],
        "skip_reasons": [],
        "repair_total": 0,
        "stats": {"batches": len(batches), "blocks": len(blocks)},
    }


def classify_blocks(state: ExtractState) -> dict[str, Any]:
    """过滤页眉页脚/目录与过短块，剩下的交给抽取器。"""
    batch = state["batches"][state["batch_index"]] if state["batches"] else []
    usable: list[dict[str, Any]] = []
    skipped = list(state.get("skipped_block_ids") or [])
    for item in batch:
        block_type = str(item.get("block_type") or "")
        content = str(item.get("content") or "").strip()
        if block_type in _SKIPPABLE_TYPES or len(content) < _MIN_CONTENT:
            skipped.append(str(item["id"]))
            continue
        usable.append(item)
    return {"batch_blocks": usable, "skipped_block_ids": skipped, "repairs": 0}


def extract_entities(state: ExtractState) -> dict[str, Any]:
    extractor = get_extractor()
    result = extractor.extract(
        state.get("batch_blocks") or [],
        state.get("classes") or {},
        state.get("relations") or {},
    )
    return {"raw": result.raw, "error": result.error, "issues": [], "route": ""}


def extract_relations(state: ExtractState) -> dict[str, Any]:
    """把关系数组规整成纯字典列表；类型错误留给 schema_validate 报出。"""
    raw = state.get("raw")
    if not isinstance(raw, dict):
        return {}
    relations = raw.get("relations")
    if not isinstance(relations, list):
        return {}
    cleaned = [item for item in relations if isinstance(item, dict)]
    return {"raw": {**raw, "relations": cleaned}}


def schema_validate(state: ExtractState) -> dict[str, Any]:
    block_ids = {str(item["id"]) for item in state.get("batch_blocks") or []}
    issues = validate_extraction(
        state.get("raw"),
        classes=state.get("classes") or {},
        relations=state.get("relations") or {},
        block_ids=block_ids,
    )
    repairs = int(state.get("repairs") or 0)
    max_repairs = int(state.get("max_repairs") or settings.extract_max_repair_attempts)

    if not issues:
        route = "commit"
    elif repairs < max_repairs:
        route = "repair"
    elif any(item.blocking for item in issues):
        route = "skip"
    else:
        route = "commit"

    return {"issues": issues_to_dicts(issues), "route": route}


def repair_output(state: ExtractState) -> dict[str, Any]:
    extractor = get_extractor()
    result = extractor.repair(
        state.get("raw"),
        state.get("issues") or [],
        state.get("batch_blocks") or [],
        state.get("classes") or {},
        state.get("relations") or {},
    )
    return {
        "raw": result.raw,
        "error": result.error,
        "repairs": int(state.get("repairs") or 0) + 1,
        "repair_total": int(state.get("repair_total") or 0) + 1,
        "route": "",
    }


def _advance(state: ExtractState, stats: dict[str, Any]) -> dict[str, Any]:
    next_index = int(state.get("batch_index") or 0) + 1
    route = "next" if next_index < len(state.get("batches") or []) else "done"
    return {
        "batch_index": next_index,
        "repairs": 0,
        "raw": None,
        "error": None,
        "issues": [],
        "stats": {**(state.get("stats") or {}), **stats},
        "route": route,
    }


def commit_batch(state: ExtractState) -> dict[str, Any]:
    block_ids = {str(item["id"]) for item in state.get("batch_blocks") or []}
    fixed, dropped = repair_extraction(
        state.get("raw"),
        classes=state.get("classes") or {},
        relations=state.get("relations") or {},
        block_ids=block_ids,
    )

    entities = list(state.get("entities") or [])
    relation_rows = list(state.get("relation_rows") or [])
    key_by_ref: dict[str, str] = {}
    for item in fixed["entities"]:
        key = f"{item['class_code']}:{normalize_name(item['name'])}"
        key_by_ref[item["ref"]] = key
        entities.append(
            {
                "class_code": item["class_code"],
                "name": item["name"],
                "normalized_name": normalize_name(item["name"]),
                "aliases": item.get("aliases") or [],
                "description": item.get("description"),
                "confidence": item.get("confidence") or 0.0,
                "evidence_block_ids": item.get("evidence_block_ids") or [],
            }
        )
    for item in fixed["relations"]:
        source_key = key_by_ref.get(item["source_ref"])
        target_key = key_by_ref.get(item["target_ref"])
        if not source_key or not target_key or source_key == target_key:
            dropped += 1
            continue
        relation_rows.append(
            {
                "source_key": source_key,
                "target_key": target_key,
                "code": item["code"],
                "confidence": item.get("confidence") or 0.0,
                "evidence_block_id": item.get("evidence_block_id"),
            }
        )

    stats = {
        "relations_dropped": int((state.get("stats") or {}).get("relations_dropped") or 0)
        + dropped
    }
    return {**_advance(state, stats), "entities": entities, "relation_rows": relation_rows}


def skip_batch(state: ExtractState) -> dict[str, Any]:
    block_ids = [str(item["id"]) for item in state.get("batch_blocks") or []]
    skipped = list(state.get("skipped_block_ids") or [])
    skipped.extend(item for item in block_ids if item not in skipped)
    reasons = list(state.get("skip_reasons") or [])
    for issue in state.get("issues") or []:
        code = str(issue.get("code"))
        if code and code not in reasons:
            reasons.append(code)
    if state.get("error") and state["error"] not in reasons:
        reasons.append(str(state["error"]))
    stats = {
        "batches_skipped": int((state.get("stats") or {}).get("batches_skipped") or 0) + 1
    }
    return {
        **_advance(state, stats),
        "skipped_block_ids": skipped,
        "skip_reasons": reasons,
    }


def dedupe_candidates(state: ExtractState) -> dict[str, Any]:
    task_id = str(state["task_id"])
    entities = dedupe_entities(state.get("entities") or [])
    id_by_key: dict[str, str] = {}
    for item in entities:
        item["id"] = entity_node_id(task_id, item["class_code"], item["name"])
        id_by_key[f"{item['class_code']}:{item['normalized_name']}"] = item["id"]

    dropped = int((state.get("stats") or {}).get("relations_dropped") or 0)
    relations: list[dict[str, Any]] = []
    for item in state.get("relation_rows") or []:
        source_id = id_by_key.get(item["source_key"])
        target_id = id_by_key.get(item["target_key"])
        if not source_id or not target_id or source_id == target_id:
            dropped += 1
            continue
        relation_id = str(
            uuid.uuid5(
                RELATION_NAMESPACE,
                f"{task_id}:{source_id}:{item['code']}:{target_id}",
            )
        )
        relations.append(
            {
                "id": relation_id,
                "source_id": source_id,
                "target_id": target_id,
                "code": item["code"],
                "confidence": float(item.get("confidence") or 0.0),
                "evidence_block_id": item.get("evidence_block_id"),
            }
        )
    relations = dedupe_relations(relations)
    stats = {
        "entities": len(entities),
        "relations": len(relations),
        "relations_dropped": dropped,
    }
    return {
        "entities": entities,
        "relation_rows": relations,
        "stats": {**(state.get("stats") or {}), **stats},
    }


def persist_candidates(state: ExtractState) -> dict[str, Any]:
    writer = get_candidate_writer()
    counts = writer(
        str(state["workspace_id"]),
        str(state["task_id"]),
        str(state["document_id"]),
        state.get("entities") or [],
        state.get("relation_rows") or [],
    )
    stats = {
        "entities_persisted": counts.get("entities", 0),
        "relations_persisted": counts.get("relations", 0),
    }
    return {"stats": {**(state.get("stats") or {}), **stats}}


def route_after_classify(state: ExtractState) -> str:
    """整块被页眉页脚/过短规则过滤掉时，不必再调模型。"""
    return "extract" if state.get("batch_blocks") else "skip"


def route_after_validate(state: ExtractState) -> str:
    return str(state.get("route") or "commit")


def route_after_batch(state: ExtractState) -> str:
    return str(state.get("route") or "done")


__all__ = [
    "chunk_blocks",
    "classify_blocks",
    "commit_batch",
    "dedupe_candidates",
    "extract_entities",
    "extract_relations",
    "persist_candidates",
    "repair_output",
    "route_after_batch",
    "route_after_classify",
    "route_after_validate",
    "schema_validate",
    "skip_batch",
]
