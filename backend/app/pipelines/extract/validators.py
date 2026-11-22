"""抽取结果的 schema 校验与修复。

校验分两类：
- 可修复：置信度越界、别名/证据类型不对、悬空证据块 → 就地纠正；
- 不可修复：本体里没有的类型、关系端点类型不匹配、ref 悬空 → 修复节点丢弃对应条目，
  丢光后整批跳过并计入 ``skipped_blocks``，避免脏数据进图。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Issue:
    code: str
    ref: str
    message: str
    blocking: bool


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_extraction(
    raw: dict[str, Any] | None,
    *,
    classes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    block_ids: set[str],
) -> list[Issue]:
    if raw is None:
        return [Issue("invalid_output", "", "抽取输出为空或不是 JSON", True)]

    issues: list[Issue] = []
    entities = raw.get("entities")
    if not isinstance(entities, list):
        return [Issue("entities_not_list", "", "entities 必须是数组", True)]

    refs: dict[str, str] = {}
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            issues.append(Issue("entity_not_object", str(index), "实体必须是对象", True))
            continue
        ref = str(entity.get("ref") or f"e{index}")
        code = str(entity.get("class_code") or "")
        name = str(entity.get("name") or "").strip()
        if not name:
            issues.append(Issue("entity_name_empty", ref, "实体名称为空", True))
        if code not in classes:
            issues.append(Issue("entity_class_unknown", ref, f"未定义的实体类 {code}", True))
        if ref in refs:
            issues.append(Issue("entity_ref_duplicate", ref, "实体 ref 重复", True))
        refs[ref] = code

        confidence = _as_float(entity.get("confidence"), -1.0)
        if not 0.0 <= confidence <= 1.0:
            issues.append(Issue("confidence_range", ref, "置信度必须在 0~1", False))
        aliases = entity.get("aliases")
        if aliases is not None and not isinstance(aliases, list):
            issues.append(Issue("aliases_type", ref, "aliases 必须是数组", False))
        evidence = entity.get("evidence_block_ids")
        if evidence is not None and not isinstance(evidence, list):
            issues.append(Issue("evidence_type", ref, "证据必须是数组", True))
        elif evidence:
            unknown = [item for item in evidence if str(item) not in block_ids]
            if unknown:
                issues.append(Issue("evidence_unknown", ref, "证据块不属于本批次", False))

    relation_list = raw.get("relations") or []
    if not isinstance(relation_list, list):
        issues.append(Issue("relations_not_list", "", "relations 必须是数组", True))
        relation_list = []
    for index, relation in enumerate(relation_list):
        if not isinstance(relation, dict):
            issues.append(Issue("relation_not_object", str(index), "关系必须是对象", True))
            continue
        code = str(relation.get("code") or "")
        source_ref = str(relation.get("source_ref") or "")
        target_ref = str(relation.get("target_ref") or "")
        if code not in relations:
            issues.append(Issue("relation_code_unknown", code, f"未定义的关系类型 {code}", True))
            continue
        if source_ref not in refs or target_ref not in refs:
            issues.append(Issue("relation_ref_unknown", code, "关系端点不存在", True))
            continue
        meta = relations[code]
        if meta.get("source_class_code") and refs[source_ref] != meta["source_class_code"]:
            issues.append(Issue("relation_endpoint_mismatch", code, "起点类型不匹配", True))
        if meta.get("target_class_code") and refs[target_ref] != meta["target_class_code"]:
            issues.append(Issue("relation_endpoint_mismatch", code, "终点类型不匹配", True))

        confidence = _as_float(relation.get("confidence"), -1.0)
        if not 0.0 <= confidence <= 1.0:
            issues.append(Issue("confidence_range", code, "置信度必须在 0~1", False))
        evidence = relation.get("evidence_block_id")
        if evidence and str(evidence) not in block_ids:
            issues.append(Issue("evidence_unknown", code, "证据块不属于本批次", False))

    return issues


def repair_extraction(
    raw: dict[str, Any] | None,
    *,
    classes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    block_ids: set[str],
) -> tuple[dict[str, Any], int]:
    """按校验规则纠正可修复项、丢弃不可修复项，返回 (结果, 丢弃条数)。"""
    if not isinstance(raw, dict):
        return {"entities": [], "relations": []}, 0

    dropped = 0
    kept_entities: list[dict[str, Any]] = []
    seen_refs: dict[str, str] = {}
    for index, entity in enumerate(raw.get("entities") or []):
        if not isinstance(entity, dict):
            dropped += 1
            continue
        code = str(entity.get("class_code") or "")
        name = str(entity.get("name") or "").strip()
        ref = str(entity.get("ref") or f"e{index}")
        if code not in classes or not name or ref in seen_refs:
            dropped += 1
            continue
        confidence = _as_float(entity.get("confidence"), 0.0)
        aliases_raw = entity.get("aliases")
        aliases = [str(item) for item in aliases_raw] if isinstance(aliases_raw, list) else []
        evidence_raw = entity.get("evidence_block_ids")
        evidence = [
            str(item)
            for item in (evidence_raw if isinstance(evidence_raw, list) else [])
            if str(item) in block_ids
        ]
        if not evidence:
            evidence = sorted(block_ids)
        seen_refs[ref] = code
        kept_entities.append(
            {
                "ref": ref,
                "class_code": code,
                "name": name,
                "aliases": aliases,
                "description": entity.get("description"),
                "confidence": min(max(confidence, 0.0), 1.0),
                "evidence_block_ids": evidence,
            }
        )

    kept_relations: list[dict[str, Any]] = []
    for relation in raw.get("relations") or []:
        if not isinstance(relation, dict):
            dropped += 1
            continue
        code = str(relation.get("code") or "")
        source_ref = str(relation.get("source_ref") or "")
        target_ref = str(relation.get("target_ref") or "")
        meta = relations.get(code)
        if meta is None or source_ref not in seen_refs or target_ref not in seen_refs:
            dropped += 1
            continue
        if meta.get("source_class_code") and seen_refs[source_ref] != meta["source_class_code"]:
            dropped += 1
            continue
        if meta.get("target_class_code") and seen_refs[target_ref] != meta["target_class_code"]:
            dropped += 1
            continue
        confidence = _as_float(relation.get("confidence"), 0.0)
        evidence_block_id = relation.get("evidence_block_id")
        if evidence_block_id is not None and str(evidence_block_id) not in block_ids:
            evidence_block_id = sorted(block_ids)[0] if block_ids else None
        kept_relations.append(
            {
                "source_ref": source_ref,
                "target_ref": target_ref,
                "code": code,
                "confidence": min(max(confidence, 0.0), 1.0),
                "evidence_block_id": str(evidence_block_id) if evidence_block_id else None,
            }
        )

    return {"entities": kept_entities, "relations": kept_relations}, dropped


def issues_to_dicts(issues: list[Issue]) -> list[dict[str, Any]]:
    return [asdict(item) for item in issues]


__all__ = ["Issue", "issues_to_dicts", "repair_extraction", "validate_extraction"]
