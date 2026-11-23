"""知识抽取的纯函数：名称归一、实体身份、候选去重、关系证据键。

放在 domain 层是为了不走数据库就能单测：入参是普通字典，
返回新的普通字典，方便在流水线节点与测试里复用。

两个身份规则（本变更的核心）：

- 实体：``identity:<标识值>``（标识键齐全且值能在证据原文中找到）或
  ``name:<归一名称>``（回退）。id 只是身份的确定性默认值，真正落库前还要按
  ``identity_key`` 回查既有节点，见 ``app/infra/graph/entity_repository.py``。
- 关系：``evidence_key`` = 文档 + 内容块 + 类型 + 两端 + 归一化证据原文，
  同一处原文只对应一条关系实例，同一对实体因此可以拥有多条同类型关系。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Iterable
from typing import Any

_SPACES = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[\s\-_·•.,，。;；:：!！?？\"'“”‘’()（）\[\]【】<>《》/\\|]+")

ENTITY_NAMESPACE = uuid.UUID("6f1a4f4e-0d9e-5d1b-9a43-2f0f3b7c9d21")
RELATION_NAMESPACE = uuid.UUID("1c6e0d38-0f6c-5b8a-9f52-6a2f6a7f4d10")

IDENTITY_SEPARATOR = "\x1f"
KIND_IDENTITY = "identity"
KIND_NAME = "name"


def normalize_name(name: str) -> str:
    """全角转半角、去空白与标点、统一小写，用于同义合并。"""
    text = unicodedata.normalize("NFKC", name or "").strip()
    text = _PUNCTUATION.sub("", text)
    return _SPACES.sub("", text).lower()


def normalize_identifier(value: Any) -> str:
    """标识值的归一化：去分隔符 + 大写。

    证件号/统一社会信用代码在原文里常带空格或连字符（``9131 0000-XXXX``），
    只有归一后比较才能跨文档对齐；名称不走这条规则（归一名称另有规则）。
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = _PUNCTUATION.sub("", text)
    return text.upper()


def _appears_in_evidence(value: str, evidence_texts: Iterable[str]) -> bool:
    if not value:
        return False
    for text in evidence_texts:
        if value and value in normalize_identifier(text):
            return True
    return False


def compute_identity(
    class_def: dict[str, Any] | None,
    attributes: dict[str, Any] | None,
    name: str,
    evidence_texts: Iterable[str] = (),
) -> tuple[str, str]:
    """返回 ``(identity_kind, identity_value)``。

    只有"标识键齐全 + 值非空 + 值能在证据原文中找到"三条同时满足才用标识身份；
    任意一条不满足就回退到归一名称，并让调用方打上``name``标记。

    第三条是关键护栏：模型幻觉出一个证号会把两个人并成一个人，
    而幻觉值几乎不可能同时出现在原文里。
    """
    keys = [str(code) for code in (class_def or {}).get("identity_keys") or [] if str(code)]
    values_map = attributes or {}
    texts = list(evidence_texts)
    if keys:
        values: list[str] = []
        complete = True
        for code in keys:
            value = normalize_identifier(values_map.get(code))
            if not value or not _appears_in_evidence(value, texts):
                complete = False
                break
            values.append(value)
        if complete and values:
            return KIND_IDENTITY, IDENTITY_SEPARATOR.join(values)
    return KIND_NAME, normalize_name(name)


def identity_key(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def entity_node_id(workspace_id: str, class_code: str, key: str) -> str:
    """身份的确定性默认 id；真正写入前会按 identity_key 回查既有节点。"""
    return str(uuid.uuid5(ENTITY_NAMESPACE, f"{workspace_id}:{class_code}:{key}"))


def relation_evidence_key(
    document_id: str,
    block_id: str | None,
    code: str,
    source_id: str,
    target_id: str,
    evidence_text: str | None,
) -> str:
    """证据键：JSON 数组编码，避免分隔符出现在证据原文里造成歧义。"""
    payload = [
        str(document_id or ""),
        str(block_id or ""),
        str(code or ""),
        str(source_id or ""),
        str(target_id or ""),
        normalize_name(evidence_text or ""),
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def relation_instance_id(evidence_key: str) -> str:
    return str(uuid.uuid5(RELATION_NAMESPACE, evidence_key))


def relation_fingerprint(evidence_key: str) -> str:
    """短指纹：给人看的去重键（日志/报告里不必塞整段 JSON）。"""
    return hashlib.sha256(evidence_key.encode("utf-8")).hexdigest()[:16]


def _merge_attributes(
    current: dict[str, Any], incoming: dict[str, Any]
) -> tuple[dict[str, Any], int]:
    """批次内合并：已存在的非空值优先，返回值与冲突个数。"""
    merged = dict(current)
    conflicts = 0
    for code, value in (incoming or {}).items():
        if value in (None, "", [], {}):
            continue
        existing = merged.get(code)
        if existing in (None, "", [], {}):
            merged[code] = value
        elif existing != value:
            conflicts += 1
    return merged, conflicts


def dedupe_entities(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (class_code, identity_key) 合并：别名/证据取并集、属性非空优先、置信度取最大。"""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        class_code = str(item.get("class_code") or "").strip()
        item_kind = str(item.get("identity_kind") or KIND_NAME)
        item_value = str(item.get("identity_value") or normalize_name(name))
        item_key = str(item.get("identity_key") or identity_key(item_kind, item_value))
        key = (class_code, item_key)
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "class_code": class_code,
                "name": name,
                "normalized_name": normalize_name(name),
                "identity_kind": item_kind,
                "identity_value": item_value,
                "identity_key": item_key,
                "aliases": list(dict.fromkeys(item.get("aliases") or [])),
                "description": item.get("description"),
                "attributes": dict(item.get("attributes") or {}),
                "confidence": float(item.get("confidence") or 0.0),
                "evidence_block_ids": list(dict.fromkeys(item.get("evidence_block_ids") or [])),
            }
            continue
        for alias in item.get("aliases") or []:
            if alias and alias not in current["aliases"]:
                current["aliases"].append(alias)
        for block_id in item.get("evidence_block_ids") or []:
            if block_id and block_id not in current["evidence_block_ids"]:
                current["evidence_block_ids"].append(block_id)
        current["confidence"] = max(current["confidence"], float(item.get("confidence") or 0.0))
        if not current.get("description") and item.get("description"):
            current["description"] = item["description"]
        current["attributes"], conflicts = _merge_attributes(
            current.get("attributes") or {}, item.get("attributes") or {}
        )
        current["attribute_conflicts"] = int(current.get("attribute_conflicts") or 0) + conflicts
    return list(merged.values())


def dedupe_relations(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按关系实例 id（即证据键）去重，同一证据取置信度最高的一条。

    不再按 (源, 目标, 类型) 收敛：同一对实体之间的多段任职是合法数据。
    """
    merged: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = str(item.get("id") or "")
        if not key:
            key = relation_evidence_key(
                str(item.get("document_id") or ""),
                item.get("evidence_block_id"),
                str(item.get("code") or ""),
                str(item.get("source_id") or ""),
                str(item.get("target_id") or ""),
                item.get("evidence_text"),
            )
        current = merged.get(key)
        if current is None or float(item.get("confidence") or 0.0) > float(
            current.get("confidence") or 0.0
        ):
            merged[key] = dict(item)
    return list(merged.values())


__all__ = [
    "ENTITY_NAMESPACE",
    "IDENTITY_SEPARATOR",
    "KIND_IDENTITY",
    "KIND_NAME",
    "RELATION_NAMESPACE",
    "compute_identity",
    "dedupe_entities",
    "dedupe_relations",
    "entity_node_id",
    "identity_key",
    "normalize_identifier",
    "normalize_name",
    "relation_evidence_key",
    "relation_fingerprint",
    "relation_instance_id",
]
