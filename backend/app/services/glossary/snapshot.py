"""快照：把判定结果压成可存进 ``parse_task.stats`` 的 JSON，并给出对外视图。

词典是派生物：可以随时重算，因此超限只截断、不报错；但截断后 ``counts`` 必须
与 ``entries`` 长度一致，否则事后看统计会得出错误结论。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from app.services.glossary.config import AliasRule
from app.services.glossary.judge import CANDIDATE, CONFIRMED, Entry

MAX_ENTRIES = 200
MAX_BYTES = 64 * 1024
DETAIL_ENTRY_LIMIT = 50

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_DISABLED = "disabled"


def serialize(
    entries: list[Entry],
    rules: tuple[AliasRule, ...],
    *,
    raw_hits: int,
    dropped: int,
    builtin_version: int | None,
) -> dict[str, Any]:
    kept = entries[:MAX_ENTRIES]
    truncated = len(entries) > len(kept)
    payload = _payload(kept, rules, raw_hits, dropped, truncated, builtin_version)
    while kept and _encoded_size(payload) > MAX_BYTES:
        kept = kept[:-1]
        truncated = True
        payload = _payload(kept, rules, raw_hits, dropped, truncated, builtin_version)
    return payload


def degraded(error: str) -> dict[str, Any]:
    """抽取失败时的占位快照：索引照常完成，原因留在统计里可查。"""
    return {
        "status": STATUS_DEGRADED,
        "error": error[:500],
        "counts": {"raw": 0, "confirmed": 0, "candidate": 0, "dropped": 0},
        "entries": [],
    }


def glossary_view(stats: Mapping[str, Any] | None) -> dict[str, Any]:
    """解析任务详情用的视图：缺省返回空对象，条目有上限，避免一次吐出整表。"""
    raw = (stats or {}).get("glossary")
    if not isinstance(raw, Mapping) or not raw:
        return {}
    entries = [item for item in (raw.get("entries") or []) if isinstance(item, Mapping)]
    view: dict[str, Any] = {}
    for key in ("status", "builtin_version", "counts", "rules", "error"):
        if raw.get(key) is not None:
            view[key] = raw[key]
    view["entries"] = entries[:DETAIL_ENTRY_LIMIT]
    view["truncated"] = bool(raw.get("truncated")) or len(entries) > DETAIL_ENTRY_LIMIT
    return view


def _payload(
    entries: list[Entry],
    rules: tuple[AliasRule, ...],
    raw_hits: int,
    dropped: int,
    truncated: bool,
    builtin_version: int | None,
) -> dict[str, Any]:
    return {
        "status": STATUS_OK,
        "builtin_version": builtin_version,
        "rules": [
            {
                "name": rule.name,
                "kind": rule.kind,
                "priority": rule.priority,
                "source": "builtin" if rule.builtin else "custom",
            }
            for rule in rules
        ],
        "counts": {
            "raw": raw_hits,
            "confirmed": sum(1 for entry in entries if entry.certainty == CONFIRMED),
            "candidate": sum(1 for entry in entries if entry.certainty == CANDIDATE),
            "dropped": dropped,
        },
        "truncated": truncated,
        "entries": [_entry(entry) for entry in entries],
    }


def _entry(entry: Entry) -> dict[str, Any]:
    return {
        "surface": entry.surface,
        "canonical": entry.canonical,
        "kind": entry.kind,
        "certainty": entry.certainty,
        "resolution": entry.resolution,
        "candidates": list(entry.candidates),
        "block_ids": list(entry.block_ids),
        "evidence_block_id": entry.evidence_block_id,
        "rule": entry.rule,
        "priority": entry.priority,
    }


def _encoded_size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


__all__ = [
    "DETAIL_ENTRY_LIMIT",
    "MAX_BYTES",
    "MAX_ENTRIES",
    "STATUS_DEGRADED",
    "STATUS_DISABLED",
    "STATUS_OK",
    "degraded",
    "glossary_view",
    "serialize",
]
