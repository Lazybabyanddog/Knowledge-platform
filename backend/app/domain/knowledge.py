"""知识抽取的纯函数：名称归一、候选去重、证据合并。

放在 domain 层是为了不走数据库就能单测：入参是普通字典，
返回新的普通字典，方便在流水线节点与测试里复用。
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable
from typing import Any

_SPACES = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[\s\-_·•.,，。;；:：!！?？\"'“”‘’()（）\[\]【】<>《》/\\|]+")

ENTITY_NAMESPACE = uuid.UUID("6f1a4f4e-0d9e-5d1b-9a43-2f0f3b7c9d21")


def normalize_name(name: str) -> str:
    """全角转半角、去空白与标点、统一小写，用于同义合并。"""
    text = unicodedata.normalize("NFKC", name or "").strip()
    text = _PUNCTUATION.sub("", text)
    return _SPACES.sub("", text).lower()


def entity_node_id(task_key: str, class_code: str, name: str) -> str:
    """按 (任务, 类, 归一名称) 生成稳定 id：重跑同一任务不会产生重复实体。"""
    key = f"{task_key}:{class_code}:{normalize_name(name)}"
    return str(uuid.uuid5(ENTITY_NAMESPACE, key))


def dedupe_entities(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (class_code, 归一名称) 合并：别名取并集、证据取并集、置信度取最大。"""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in candidates:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        class_code = str(item.get("class_code") or "").strip()
        key = (class_code, normalize_name(name))
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "class_code": class_code,
                "name": name,
                "normalized_name": normalize_name(name),
                "aliases": list(dict.fromkeys(item.get("aliases") or [])),
                "description": item.get("description"),
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
    return list(merged.values())


def dedupe_relations(candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一对端点 + 同一关系类型只保留置信度最高的一条。"""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in candidates:
        key = (str(item.get("source_id")), str(item.get("target_id")), str(item.get("code")))
        current = merged.get(key)
        if current is None or float(item.get("confidence") or 0.0) > float(
            current.get("confidence") or 0.0
        ):
            merged[key] = dict(item)
    return list(merged.values())


__all__ = [
    "ENTITY_NAMESPACE",
    "dedupe_entities",
    "dedupe_relations",
    "entity_node_id",
    "normalize_name",
]
