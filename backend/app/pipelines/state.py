"""解析图的共享状态：所有节点读写同一份 TypedDict，便于单测与断点观察。"""

from __future__ import annotations

from typing import Any, TypedDict

from app.parsing.base import ParsedBlock


class ParseState(TypedDict, total=False):
    task_id: str
    file_name: str
    mime_type: str
    doc_type: str
    data: bytes
    config: dict[str, Any]
    engine: str
    blocks: list[ParsedBlock]
    warnings: list[str]
    stats: dict[str, Any]
    quality: dict[str, Any]
    error: str | None
