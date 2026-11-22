"""抽取图的共享状态。"""

from __future__ import annotations

from typing import Any, TypedDict


class ExtractState(TypedDict, total=False):
    task_id: str
    workspace_id: str
    document_id: str
    blocks: list[dict[str, Any]]
    classes: dict[str, dict[str, Any]]
    relations: dict[str, dict[str, Any]]
    batch_size: int
    max_repairs: int

    batches: list[list[dict[str, Any]]]
    batch_index: int
    batch_blocks: list[dict[str, Any]]
    repairs: int

    raw: dict[str, Any] | None
    error: str | None
    issues: list[dict[str, Any]]
    route: str

    entities: list[dict[str, Any]]
    relation_rows: list[dict[str, Any]]

    skipped_block_ids: list[str]
    skip_reasons: list[str]
    repair_total: int

    stats: dict[str, Any]


__all__ = ["ExtractState"]
