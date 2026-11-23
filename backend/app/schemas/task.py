"""解析与抽取共用的任务可观测结构：错误、进度、可用操作。

前端按这些字段渲染"为什么失败、卡在哪、还能做什么"，不再自己拼错误文案。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskErrorInfo(BaseModel):
    code: str
    message: str
    hint: str
    retryable: bool = False


class TaskProgress(BaseModel):
    """批次进度：解析任务不用，恒为 0。"""

    batches_total: int = 0
    batches_done: int = 0
    batches_skipped: int = 0
    current_batch: int | None = None


class TaskActions(BaseModel):
    can_revive: bool = False
    can_skip_failed_batches: bool = False
    revive_remaining: int = 0
    auto_retry_remaining: int = 0


class TaskSkipReason(BaseModel):
    reason: str
    count: int


class TaskAttemptBase(BaseModel):
    id: str
    attempt_no: int
    status: str
    source: str = "manual"
    stats: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    error_detail: dict[str, Any] | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


__all__ = [
    "TaskActions",
    "TaskAttemptBase",
    "TaskErrorInfo",
    "TaskProgress",
    "TaskSkipReason",
]
