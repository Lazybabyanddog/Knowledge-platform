"""抽取结果的 schema 校验与修复。

校验分两类：
- 可修复：置信度越界、别名/证据类型不对、悬空证据块 → 就地纠正；
- 不可修复：本体里没有的类型、关系端点类型不匹配、ref 悬空 → 修复节点丢弃对应条目，
  丢光后整批跳过并计入 ``skipped_blocks``，避免脏数据进图。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from app.domain.knowledge import normalize_name

# 属性类问题只记录、不触发修复回合：修复要花一次模型调用，而属性值本身
# 可以在本地确定性地丢弃/强转，为它多跑一轮修复不值得。
SOFT_ISSUE_CODES = frozenset(
    {
        "attr_unknown",
        "attr_type_mismatch",
        "attr_enum_mismatch",
        "attr_required_missing",
        "attr_not_object",
        "evidence_text_mismatch",
    }
)

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日")
_INT_PATTERN = re.compile(r"[+-]?\d+")
_BOOL_TRUE = {"true", "1", "是", "yes", "y"}
_BOOL_FALSE = {"false", "0", "否", "no", "n"}
_INVALID = object()


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


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value in ([], {})


def _coerce_number(value: Any) -> Any:
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip().replace(",", "")
    try:
        return int(text) if _INT_PATTERN.fullmatch(text) else float(text)
    except ValueError:
        return _INVALID


def _coerce_date(value: Any) -> Any:
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return _INVALID


def _coerce_boolean(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    return _INVALID


def _coerce_scalar(type_name: str, value: Any, enum_values: list[str] | None) -> Any:
    if type_name == "number":
        return _coerce_number(value)
    if type_name == "date":
        return _coerce_date(value)
    if type_name == "boolean":
        return _coerce_boolean(value)
    if type_name == "enum":
        options = [item for item in (enum_values or []) if item]
        text = str(value).strip()
        for option in options:
            if text.casefold() == option.strip().casefold():
                return option
        return _INVALID
    if isinstance(value, (dict, list)):
        return _INVALID
    return str(value).strip()


def _coerce_value(defn: dict[str, Any], value: Any) -> Any:
    type_name = str(defn.get("type") or "string")
    enum_values = defn.get("enum_values") or []
    if type_name == "array":
        if not isinstance(value, list):
            return _INVALID
        item_type = str(defn.get("items_type") or "string")
        items: list[Any] = []
        for entry in value:
            if _is_empty(entry):
                continue
            normalized = _coerce_scalar(item_type, entry, None)
            if normalized is not _INVALID:
                items.append(normalized)
        return items or _INVALID
    return _coerce_scalar(type_name, value, list(enum_values) if enum_values else None)


def coerce_attributes(
    raw: Any, defs: dict[str, Any] | None
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """按本体定义强转属性值，返回 ``(干净属性, [(属性编码, issue 码)])``。

    未知编码、类型不符、枚举不符、数组项全不合规 → 丢弃该属性值；
    必填缺失 → 记一条非阻塞问题。任何情况下都不丢实体/关系本身。
    """
    definitions = {
        str(code): (spec if isinstance(spec, dict) else {}) for code, spec in (defs or {}).items()
    }
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {}, [("*", "attr_not_object")]

    cleaned: dict[str, Any] = {}
    problems: list[tuple[str, str]] = []
    for code, value in raw.items():
        name = str(code)
        defn = definitions.get(name)
        if defn is None:
            problems.append((name, "attr_unknown"))
            continue
        if _is_empty(value):
            continue
        normalized = _coerce_value(defn, value)
        if normalized is _INVALID:
            kind = "attr_enum_mismatch" if defn.get("type") == "enum" else "attr_type_mismatch"
            problems.append((name, kind))
            continue
        cleaned[name] = normalized

    for code, defn in definitions.items():
        if defn.get("required") and code not in cleaned:
            problems.append((code, "attr_required_missing"))
    return cleaned, problems


def clean_evidence_text(value: Any, block_text: str | None) -> tuple[str | None, bool]:
    """返回 ``(可落库的原文片段, 是否不匹配)``。

    模型抄错一句原文并不可怕，可怕的是这句原文成了关系身份的一部分，
    所以只有在证据块正文里能找到时才保留。
    """
    if _is_empty(value):
        return None, False
    text = str(value).strip()
    if not block_text:
        return None, True
    if normalize_name(text) and normalize_name(text) in normalize_name(block_text):
        return text, False
    return None, True


def validate_extraction(
    raw: dict[str, Any] | None,
    *,
    classes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    block_ids: set[str],
    block_texts: dict[str, str] | None = None,
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

        class_def = classes.get(code) or {}
        _, attr_problems = coerce_attributes(entity.get("attributes"), class_def.get("attributes"))
        for attr_code, issue_code in attr_problems:
            issues.append(
                Issue(
                    issue_code,
                    ref,
                    f"实体属性 {attr_code} 未通过校验（{issue_code}）",
                    False,
                )
            )

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

        relation_def = meta or {}
        _, attr_problems = coerce_attributes(
            relation.get("attributes"), relation_def.get("attributes")
        )
        for attr_code, issue_code in attr_problems:
            issues.append(
                Issue(
                    issue_code,
                    code,
                    f"关系属性 {attr_code} 未通过校验（{issue_code}）",
                    False,
                )
            )

        raw_text = relation.get("evidence_text")
        if raw_text is not None and block_texts is not None:
            block_text = block_texts.get(str(evidence)) if evidence else None
            if block_text is None:
                block_text = "\n".join(block_texts.values())
            _, mismatch = clean_evidence_text(raw_text, block_text)
            if mismatch:
                issues.append(
                    Issue("evidence_text_mismatch", code, "证据原文不在所属内容块中", False)
                )

    return issues


def repair_extraction(
    raw: dict[str, Any] | None,
    *,
    classes: dict[str, dict[str, Any]],
    relations: dict[str, dict[str, Any]],
    block_ids: set[str],
    block_texts: dict[str, str] | None = None,
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
        class_def = classes.get(code) or {}
        attributes, _ = coerce_attributes(entity.get("attributes"), class_def.get("attributes"))
        kept_entities.append(
            {
                "ref": ref,
                "class_code": code,
                "name": name,
                "aliases": aliases,
                "description": entity.get("description"),
                "attributes": attributes,
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
        attributes, _ = coerce_attributes(relation.get("attributes"), meta.get("attributes"))
        block_text = None
        if block_texts is not None and evidence_block_id:
            block_text = block_texts.get(str(evidence_block_id))
        evidence_text, _ = clean_evidence_text(relation.get("evidence_text"), block_text)
        kept_relations.append(
            {
                "source_ref": source_ref,
                "target_ref": target_ref,
                "code": code,
                "attributes": attributes,
                "evidence_text": evidence_text,
                "confidence": min(max(confidence, 0.0), 1.0),
                "evidence_block_id": str(evidence_block_id) if evidence_block_id else None,
            }
        )

    return {"entities": kept_entities, "relations": kept_relations}, dropped


def issues_to_dicts(issues: list[Issue]) -> list[dict[str, Any]]:
    return [asdict(item) for item in issues]


__all__ = [
    "SOFT_ISSUE_CODES",
    "Issue",
    "clean_evidence_text",
    "coerce_attributes",
    "issues_to_dicts",
    "repair_extraction",
    "validate_extraction",
]
