"""抽取图节点。

流程：分块 → 分类 → 抽实体 → 抽关系 → 校验 →（修复 ⇄ 校验）→ 提交/跳过 → 去重 → 落库。
每个节点只改自己关心的字段，路由由 ``route_*`` 读取 ``state["route"]``。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.domain.knowledge import (
    compute_identity,
    dedupe_entities,
    dedupe_relations,
    entity_node_id,
    identity_key,
    normalize_name,
    relation_evidence_key,
    relation_instance_id,
)
from app.pipelines.extract.extractor import get_extractor
from app.pipelines.extract.state import ExtractState
from app.pipelines.extract.validators import (
    SOFT_ISSUE_CODES,
    issues_to_dicts,
    repair_extraction,
    validate_extraction,
)
from app.pipelines.extract.writer import get_candidate_writer

logger = logging.getLogger(__name__)

_SKIPPABLE_TYPES = {"footer", "header", "toc"}
_MIN_CONTENT = 4


def _block_texts(state: ExtractState) -> dict[str, str]:
    """证据原文校验与标识值落地校验都要用块正文。"""
    return {
        str(item["id"]): str(item.get("content") or "")
        for item in state.get("batch_blocks") or []
        if item.get("id")
    }


def chunk_blocks(state: ExtractState) -> dict[str, Any]:
    """批次由块列表 + 批大小确定性重算，因此 ``batch_index`` 可以直接当断点游标。

    恢复执行时 ``resume_index`` 指向第一个未完成的批；已经拼回 state 的产出
    （entities / relation_rows / skipped / stats）必须保留，不能被清空。
    """
    blocks = [item for item in state.get("blocks") or [] if str(item.get("content") or "").strip()]
    size = int(state.get("batch_size") or settings.extract_batch_size)
    batches = [blocks[start : start + size] for start in range(0, len(blocks), size)]
    resume_index = max(0, int(state.get("resume_index") or 0))
    stats = {**(state.get("stats") or {}), "batches": len(batches), "blocks": len(blocks)}
    return {
        "batches": batches,
        "batch_index": min(resume_index, len(batches)),
        "entities": list(state.get("entities") or []),
        "relation_rows": list(state.get("relation_rows") or []),
        "skipped_block_ids": list(state.get("skipped_block_ids") or []),
        "skip_reasons": list(state.get("skip_reasons") or []),
        "repair_total": int(state.get("repair_total") or 0),
        "stats": stats,
    }


def route_after_chunk(state: ExtractState) -> str:
    """全部批次都已完成（或恢复位置在末尾）时直接去落库，不要再跑一次空批。"""
    return "extract" if int(state.get("batch_index") or 0) < len(
        state.get("batches") or []
    ) else "done"


def _write_checkpoint(
    state: ExtractState,
    *,
    batch_index: int,
    status: str,
    block_ids: list[str],
    payload: dict[str, Any],
    reason: str | None = None,
) -> None:
    """断点写入失败就让任务失败：丢进度比丢数据好，重试会从上一批继续。"""
    writer = state.get("checkpoint_writer")
    if writer is None:
        return
    writer(
        batch_index=batch_index,
        status=status,
        block_ids=block_ids,
        payload=payload,
        reason=reason,
    )


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
        block_texts=_block_texts(state),
    )
    repairs = int(state.get("repairs") or 0)
    max_repairs = int(state.get("max_repairs") or settings.extract_max_repair_attempts)

    # 属性类问题不烧修复回合：值在本地确定性丢弃即可，为此多调一次模型不值得。
    hard_issues = [item for item in issues if item.code not in SOFT_ISSUE_CODES]
    if not hard_issues:
        route = "commit"
    elif repairs < max_repairs:
        route = "repair"
    elif any(item.blocking for item in hard_issues):
        route = "skip"
    else:
        route = "commit"

    counts = dict(state.get("issue_counts") or {})
    for item in issues:
        counts[item.code] = int(counts.get(item.code) or 0) + 1
    return {"issues": issues_to_dicts(issues), "route": route, "issue_counts": counts}


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
    batch_index = int(state.get("batch_index") or 0)
    entity_offset = len(state.get("entities") or [])
    relation_offset = len(state.get("relation_rows") or [])
    block_ids = {str(item["id"]) for item in state.get("batch_blocks") or []}
    block_texts = _block_texts(state)
    classes = state.get("classes") or {}
    fixed, dropped = repair_extraction(
        state.get("raw"),
        classes=classes,
        relations=state.get("relations") or {},
        block_ids=block_ids,
        block_texts=block_texts,
    )

    entities = list(state.get("entities") or [])
    relation_rows = list(state.get("relation_rows") or [])
    key_by_ref: dict[str, str] = {}
    for item in fixed["entities"]:
        class_def = classes.get(item["class_code"]) or {}
        evidence_texts = [
            block_texts.get(str(block_id), "") for block_id in item.get("evidence_block_ids") or []
        ]
        kind, value = compute_identity(
            class_def, item.get("attributes") or {}, item["name"], evidence_texts
        )
        entity_key = identity_key(kind, value)
        key_by_ref[item["ref"]] = f"{item['class_code']}:{entity_key}"
        entities.append(
            {
                "class_code": item["class_code"],
                "name": item["name"],
                "normalized_name": normalize_name(item["name"]),
                "identity_kind": kind,
                "identity_value": value,
                "identity_key": entity_key,
                "aliases": item.get("aliases") or [],
                "description": item.get("description"),
                "attributes": item.get("attributes") or {},
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
                "attributes": item.get("attributes") or {},
                "evidence_text": item.get("evidence_text"),
                "confidence": item.get("confidence") or 0.0,
                "evidence_block_id": item.get("evidence_block_id"),
            }
        )

    stats = {
        "relations_dropped": int((state.get("stats") or {}).get("relations_dropped") or 0)
        + dropped
    }
    _write_checkpoint(
        state,
        batch_index=batch_index,
        status="committed",
        block_ids=sorted(block_ids),
        payload={
            "entities": entities[entity_offset:],
            "relation_rows": relation_rows[relation_offset:],
        },
    )
    return {**_advance(state, stats), "entities": entities, "relation_rows": relation_rows}


def skip_batch(state: ExtractState) -> dict[str, Any]:
    batch_index = int(state.get("batch_index") or 0)
    block_ids = [str(item["id"]) for item in state.get("batch_blocks") or []]
    skipped = list(state.get("skipped_block_ids") or [])
    skipped.extend(item for item in block_ids if item not in skipped)
    reasons = list(state.get("skip_reasons") or [])
    new_reasons: list[str] = []
    for issue in state.get("issues") or []:
        code = str(issue.get("code"))
        if code and code not in reasons and code not in new_reasons:
            new_reasons.append(code)
    if state.get("error"):
        raw = str(state["error"])
        if raw not in reasons and raw not in new_reasons:
            new_reasons.append(raw)
    reasons.extend(new_reasons)
    stats = {
        "batches_skipped": int((state.get("stats") or {}).get("batches_skipped") or 0) + 1
    }
    _write_checkpoint(
        state,
        batch_index=batch_index,
        status="skipped",
        block_ids=block_ids,
        payload={},
        reason=", ".join(new_reasons) if new_reasons else "batch_skipped",
    )
    return {
        **_advance(state, stats),
        "skipped_block_ids": skipped,
        "skip_reasons": reasons,
    }


def dedupe_candidates(state: ExtractState) -> dict[str, Any]:
    workspace_id = str(state["workspace_id"])
    document_id = str(state["document_id"])
    entities = dedupe_entities(state.get("entities") or [])
    id_by_key: dict[str, str] = {}
    conflicts = 0
    for item in entities:
        # 默认 id 由工作空间级身份决定；真正落库前 writer 还会按 identity_key 回查既有节点。
        item["id"] = entity_node_id(workspace_id, item["class_code"], item["identity_key"])
        id_by_key[f"{item['class_code']}:{item['identity_key']}"] = item["id"]
        conflicts += int(item.pop("attribute_conflicts", 0) or 0)

    dropped = int((state.get("stats") or {}).get("relations_dropped") or 0)
    relations: list[dict[str, Any]] = []
    for item in state.get("relation_rows") or []:
        source_id = id_by_key.get(item["source_key"])
        target_id = id_by_key.get(item["target_key"])
        if not source_id or not target_id or source_id == target_id:
            dropped += 1
            continue
        evidence_key = relation_evidence_key(
            document_id,
            item.get("evidence_block_id"),
            item["code"],
            source_id,
            target_id,
            item.get("evidence_text"),
        )
        relations.append(
            {
                "id": relation_instance_id(evidence_key),
                "evidence_key": evidence_key,
                "source_id": source_id,
                "target_id": target_id,
                # 端点身份键随行传递：writer 解析出真实节点后要按它重挂关系。
                "source_key": item["source_key"],
                "target_key": item["target_key"],
                "code": item["code"],
                "attributes": item.get("attributes") or {},
                "evidence_text": item.get("evidence_text"),
                "confidence": float(item.get("confidence") or 0.0),
                "evidence_block_id": item.get("evidence_block_id"),
                "document_id": document_id,
            }
        )
    relations = dedupe_relations(relations)
    stats = {
        "entities": len(entities),
        "relations": len(relations),
        "relations_dropped": dropped,
        "attribute_conflicts": conflicts,
        "issue_counts": dict(state.get("issue_counts") or {}),
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
        "attribute_conflicts": counts.get("attribute_conflicts", 0),
        "persist_lock_degraded": counts.get("persist_lock_degraded", 0),
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
