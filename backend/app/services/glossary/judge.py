"""判定器：把原始命中收敛成条目，并只允许合法状态组合存在。

``certainty`` / ``resolution`` / ``cardinality`` 三个字段各管一件事，不做"一个 tier
承担所有含义"。一对多、规则冲突、缺证据三种情况一律降级为候选；降不下去就丢。
这里没有"首现优先""最近优先"——那等于把不确定伪装成确定。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.services.glossary.config import (
    CARDINALITY_MANY,
    CARDINALITY_ONE,
    CARDINALITY_UNKNOWN,
    KIND_RELATION,
)
from app.services.glossary.extract import RawHit

CONFIRMED = "CONFIRMED"
CANDIDATE = "CANDIDATE"
RESOLVED = "RESOLVED"
PENDING = "PENDING"

MAX_CANDIDATES = 5
MAX_BLOCK_IDS = 20


@dataclass(frozen=True, slots=True)
class Entry:
    surface: str
    kind: str
    certainty: str
    resolution: str
    block_ids: tuple[str, ...]
    rule: str
    priority: int
    canonical: str | None = None
    candidates: tuple[str, ...] = ()
    evidence_block_id: str | None = None


def is_legal_combination(certainty: str, resolution: str, cardinality: str) -> bool:
    """状态矩阵：非法组合（如 MANY + CONFIRMED）在写入前就被拦掉。"""
    if cardinality == CARDINALITY_UNKNOWN:
        return False
    if certainty == CONFIRMED:
        return resolution == RESOLVED and cardinality == CARDINALITY_ONE
    if certainty == CANDIDATE:
        return resolution == PENDING and cardinality in (CARDINALITY_ONE, CARDINALITY_MANY)
    return False


def judge(hits: Iterable[RawHit]) -> tuple[list[Entry], int]:
    """返回（条目，被丢弃的原始命中数）。条目按优先级降序，末尾最容易被截断。"""
    groups: dict[str, list[RawHit]] = {}
    for hit in hits:
        groups.setdefault(hit.surface, []).append(hit)

    entries: list[Entry] = []
    dropped = 0
    for surface, group in groups.items():
        entry, unused = _judge_group(surface, group)
        dropped += unused
        if entry is not None:
            entries.append(entry)

    entries.sort(key=lambda item: (-item.priority, item.surface))
    return entries, dropped


def _judge_group(surface: str, group: list[RawHit]) -> tuple[Entry | None, int]:
    head = group[0]
    cardinality = _normalize_cardinality(head.cardinality)
    block_ids = _unique(hit.block_id for hit in group)[:MAX_BLOCK_IDS]

    if head.kind == KIND_RELATION:
        candidates = _unique(hit.target for hit in group if hit.target)
        if not candidates:
            return None, len(group)
        entry = Entry(
            surface=surface,
            kind=head.kind,
            certainty=CANDIDATE,
            resolution=PENDING,
            block_ids=block_ids,
            rule=head.rule,
            priority=head.priority,
            candidates=tuple(candidates[:MAX_CANDIDATES]),
        )
        return (entry, 0) if _legal(entry, cardinality) else (None, len(group))

    named = [hit for hit in group if hit.canonical]
    if not named:
        return None, len(group)

    # 高优先级规则先说话：低优先级的同位置结论直接丢弃，而不是降级后混进候选
    top_priority = max(hit.priority for hit in named)
    kept = [hit for hit in named if hit.priority == top_priority]
    unused = len(group) - len(kept)

    canonicals = _unique(hit.canonical for hit in kept)
    evidence = (kept[0].block_id or None) if kept else None
    if len(canonicals) == 1 and cardinality == CARDINALITY_ONE and evidence:
        entry = Entry(
            surface=surface,
            kind=head.kind,
            certainty=CONFIRMED,
            resolution=RESOLVED,
            block_ids=block_ids,
            rule=kept[0].rule,
            priority=top_priority,
            canonical=canonicals[0],
            evidence_block_id=evidence,
        )
    else:
        entry = Entry(
            surface=surface,
            kind=head.kind,
            certainty=CANDIDATE,
            resolution=PENDING,
            block_ids=block_ids,
            rule=kept[0].rule,
            priority=top_priority,
            canonical=canonicals[0] if len(canonicals) == 1 else None,
            candidates=tuple(canonicals[:MAX_CANDIDATES]),
            evidence_block_id=evidence,
        )
    return (entry, unused) if _legal(entry, cardinality) else (None, len(group))


def _legal(entry: Entry, cardinality: str) -> bool:
    return is_legal_combination(entry.certainty, entry.resolution, cardinality)


def _normalize_cardinality(value: str) -> str:
    """UNKNOWN 不知道就按 MANY 走最保守的分支，绝不当成 ONE 去猜。"""
    return CARDINALITY_MANY if value == CARDINALITY_UNKNOWN else value


def _unique(values: Iterable[str | None]) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen


__all__ = [
    "CANDIDATE",
    "CONFIRMED",
    "PENDING",
    "RESOLVED",
    "Entry",
    "is_legal_combination",
    "judge",
]
