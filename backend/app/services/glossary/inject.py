"""读侧：把词典条目按片段裁剪成注入行。

两条硬约束：只注入片段里**实际出现**的别名；注入不进入引用片段（snippet），
片段正文与编号一字不动。
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db.models.parse_task import ParseTask
from app.services.glossary.config import KIND_ALIAS, KIND_ROLE
from app.services.glossary.judge import CONFIRMED
from app.services.glossary.snapshot import STATUS_OK

if TYPE_CHECKING:  # pragma: no cover - 只用于类型标注，避免与检索服务循环导入
    from app.services.retrieval_service import ChunkHit

MAX_LINES_PER_HIT = 5
MAX_CANDIDATES_IN_TEXT = 3
PREFIX_ALIAS = "本文简称："
PREFIX_GENERAL = "本文用语："


@dataclass(frozen=True, slots=True)
class GlossaryLine:
    surface: str
    text: str
    kind: str
    certainty: str
    priority: int


def attach_glossary(
    session: Session,
    hits: Sequence[ChunkHit],
    *,
    limit: int = MAX_LINES_PER_HIT,
) -> list[ChunkHit]:
    """一次查询取回所有相关词典，再逐片段裁剪；没有词典时原样返回。"""
    items = list(hits)
    task_ids = _task_ids(items)
    if not task_ids:
        return items
    tasks = session.scalars(select(ParseTask).where(ParseTask.id.in_(task_ids))).all()
    entries_by_task = {str(task.id): _entries(task.stats) for task in tasks}
    return [
        dataclasses.replace(
            hit,
            glossary=_lines_for(hit, entries_by_task.get(hit.parse_task_id or "", ()), limit),
        )
        for hit in items
    ]


def render_glossary(hit: ChunkHit) -> str:
    """拼成一行对照；没有条目时返回空串，调用方据此保持原始格式。"""
    if not hit.glossary:
        return ""
    alias_only = all(line.kind in (KIND_ALIAS, KIND_ROLE) for line in hit.glossary)
    prefix = PREFIX_ALIAS if alias_only else PREFIX_GENERAL
    return prefix + "；".join(line.text for line in hit.glossary) + "。"


def _lines_for(
    hit: ChunkHit, entries: Sequence[Mapping[str, Any]], limit: int
) -> tuple[GlossaryLine, ...]:
    matched = [entry for entry in entries if _appears_in(hit, entry)]
    matched.sort(key=lambda entry: (entry.get("certainty") != CONFIRMED, -_priority(entry)))
    lines: list[GlossaryLine] = []
    for entry in matched:
        text = _text(entry)
        if not text:
            continue
        lines.append(
            GlossaryLine(
                surface=str(entry.get("surface") or ""),
                text=text,
                kind=str(entry.get("kind") or ""),
                certainty=str(entry.get("certainty") or ""),
                priority=_priority(entry),
            )
        )
        if len(lines) >= limit:
            break
    return tuple(lines)


def _appears_in(hit: ChunkHit, entry: Mapping[str, Any]) -> bool:
    surface = str(entry.get("surface") or "")
    if not surface:
        return False
    block_ids = {str(item) for item in entry.get("block_ids") or ()}
    if block_ids & {str(item) for item in hit.block_ids}:
        return True
    return surface in hit.content


def _text(entry: Mapping[str, Any]) -> str:
    surface = str(entry.get("surface") or "")
    if entry.get("certainty") == CONFIRMED:
        canonical = str(entry.get("canonical") or "")
        return f"{surface} = {canonical}" if canonical else ""
    candidates = [str(item) for item in entry.get("candidates") or () if item]
    if not candidates:
        return ""
    joined = " / ".join(candidates[:MAX_CANDIDATES_IN_TEXT])
    return f"{surface} 可能指 {joined}"


def _entries(stats: Any) -> tuple[Mapping[str, Any], ...]:
    raw = (stats or {}).get("glossary") if isinstance(stats, Mapping) else None
    if not isinstance(raw, Mapping) or raw.get("status") != STATUS_OK:
        return ()
    return tuple(
        entry for entry in raw.get("entries") or () if isinstance(entry, Mapping)
    )


def _task_ids(hits: Sequence[ChunkHit]) -> list[uuid.UUID]:
    values: list[uuid.UUID] = []
    for hit in hits:
        raw = getattr(hit, "parse_task_id", None)
        if not raw:
            continue
        try:
            value = uuid.UUID(str(raw))
        except (TypeError, ValueError):
            continue
        if value not in values:
            values.append(value)
    return values


def _priority(entry: Mapping[str, Any]) -> int:
    try:
        return int(entry.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "MAX_LINES_PER_HIT",
    "PREFIX_ALIAS",
    "PREFIX_GENERAL",
    "GlossaryLine",
    "attach_glossary",
    "render_glossary",
]
