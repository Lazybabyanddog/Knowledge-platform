"""存量重抽的请求与批次响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BackfillRequest(BaseModel):
    """重抽范围：显式文档集合 / 实体类集合 / 抽取时间范围，可交叉限定。"""

    document_ids: list[str] = Field(default_factory=list, max_length=200)
    class_codes: list[str] = Field(default_factory=list, max_length=80)
    created_from: datetime | None = None
    created_to: datetime | None = None
    """只挑本体版本落后于当前版本的文档。"""
    lagging_only: bool = False
    dry_run: bool = False
    """已按新规则重抽过的文档也再跑一遍。"""
    force: bool = False
    limit: int | None = Field(default=None, ge=1, le=500)


class BackfillSkipItem(BaseModel):
    document_id: str
    reason: str
    detail: str | None = None


class BackfillFailureItem(BaseModel):
    document_id: str
    error_code: str | None = None
    error_message: str | None = None


class BackfillUnmatchedItem(BaseModel):
    kind: str
    id: str
    reason: str


class BackfillReport(BaseModel):
    documents_total: int = 0
    documents_processed: int = 0
    documents_skipped: int = 0
    documents_failed: int = 0
    extraction_tasks: int = 0
    entities: int = 0
    relations: int = 0
    reused_confirmed: int = 0
    reused_rejected: int = 0
    skipped: list[BackfillSkipItem] = Field(default_factory=list)
    failures: list[BackfillFailureItem] = Field(default_factory=list)
    unmatched: list[BackfillUnmatchedItem] = Field(default_factory=list)


class BackfillBatchItem(BaseModel):
    id: str
    status: str
    dry_run: bool
    force: bool
    scope: dict[str, Any] = Field(default_factory=dict)
    report: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
