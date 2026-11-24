"""问答反馈与治理队列的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

QaRatingLiteral = Literal["like", "dislike"]
QaDislikeReasonLiteral = Literal[
    "off_topic", "fake_citation", "outdated", "incomplete", "other"
]
QaIssueStatusLiteral = Literal["pending", "closed"]
QaDispositionLiteral = Literal[
    "false_positive", "knowledge_gap", "retrieval_failure", "answer_error"
]


class QaFeedbackRequest(BaseModel):
    rating: QaRatingLiteral
    reason: QaDislikeReasonLiteral | None = None
    note: str | None = Field(default=None, max_length=1000)


class QaFeedbackItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    record_id: str
    rating: QaRatingLiteral
    reason: str | None = None
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class QaFeedbackResponse(BaseModel):
    """没有评价时返回 ``feedback: null``，前端据此决定是否高亮反馈按钮。"""

    feedback: QaFeedbackItem | None = None


class QaSourceItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    index: int = 0
    chunk_id: str | None = None
    document_id: str | None = None
    title: str | None = None
    page_no: int | None = None
    snippet: str | None = None
    score: float | None = None
    sensitivity: str | None = None


class QaIssueItem(BaseModel):
    id: str
    representative_question: str
    record_count: int
    dislike_count: int = 0
    reasons: list[str] = Field(default_factory=list)
    sensitivity: str
    status: QaIssueStatusLiteral
    disposition: str | None = None
    assignee_id: str | None = None
    assignee_name: str | None = None
    primary_document_id: str | None = None
    primary_document_title: str | None = None
    hit_count: int | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime | None = None


class QaIssueListResponse(BaseModel):
    items: list[QaIssueItem]
    total: int
    page: int
    page_size: int


class QaRecordItem(BaseModel):
    id: str
    question: str
    answer_excerpt: str
    answer_status: str
    reasons: list[str] = Field(default_factory=list)
    sensitivity: str
    user_name: str | None = None
    hit_count: int = 0
    top_score: float | None = None
    sources: list[QaSourceItem] = Field(default_factory=list)
    created_at: datetime
    finished_at: datetime | None = None


class QaIssueEventItem(BaseModel):
    id: str
    action: str
    actor_name: str | None = None
    note: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class QaIssueDetail(QaIssueItem):
    records: list[QaRecordItem] = Field(default_factory=list)
    events: list[QaIssueEventItem] = Field(default_factory=list)


class QaAssignRequest(BaseModel):
    assignee_id: str | None = None
    note: str | None = Field(default=None, max_length=500)


class QaDisposeRequest(BaseModel):
    disposition: QaDispositionLiteral
    note: str | None = Field(default=None, max_length=500)


class QaCloseRequest(BaseModel):
    disposition: QaDispositionLiteral | None = None
    note: str | None = Field(default=None, max_length=500)


class QaReopenRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class QaAssigneeItem(BaseModel):
    id: str
    display_name: str
    username: str
