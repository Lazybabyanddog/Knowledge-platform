"""抽取图的共享状态。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

# checkpoint_writer(batch_index=..., status=..., block_ids=..., payload=..., reason=...)
CheckpointWriter = Callable[..., None]


class ExtractState(TypedDict, total=False):
    task_id: str
    workspace_id: str
    document_id: str
    blocks: list[dict[str, Any]]
    classes: dict[str, dict[str, Any]]
    relations: dict[str, dict[str, Any]]
    batch_size: int
    max_repairs: int

    # 断点：resume_index 是"从第几批开始"，writer 由服务层注入（图节点保持纯函数）
    resume_index: int
    attempt_no: int
    checkpoint_writer: CheckpointWriter

    batches: list[list[dict[str, Any]]]
    batch_index: int
    batch_blocks: list[dict[str, Any]]
    repairs: int

    raw: dict[str, Any] | None
    error: str | None
    issues: list[dict[str, Any]]
    # 属性类问题的累计计数：这些问题不触发修复回合，但要进任务统计
    issue_counts: dict[str, int]
    route: str

    entities: list[dict[str, Any]]
    relation_rows: list[dict[str, Any]]

    skipped_block_ids: list[str]
    skip_reasons: list[str]
    repair_total: int

    stats: dict[str, Any]


__all__ = ["ExtractState"]
