"""审批流、审批实例与审批轨迹的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ReviewStatusLiteral = Literal["pending", "approved", "rejected", "withdrawn"]
ReviewActionLiteral = Literal["approve", "reject", "withdraw"]
DocTypeLiteral = Literal["pdf", "office", "text", "image", "html"]
ApproverTypeLiteral = Literal["role", "user"]

MAX_STEPS = 10


class ReviewStepInput(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    approver_type: ApproverTypeLiteral = "role"
    approver_value: str = Field(min_length=1, max_length=64)
    skippable: bool = False

    @field_validator("name", "approver_value")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ReviewStepItem(ReviewStepInput):
    step_no: int


class ReviewFlowCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    doc_type: DocTypeLiteral
    description: str | None = Field(default=None, max_length=500)
    steps: list[ReviewStepInput] = Field(min_length=1, max_length=MAX_STEPS)
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return value.strip()


class ReviewFlowUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    steps: list[ReviewStepInput] | None = Field(default=None, min_length=1, max_length=MAX_STEPS)
    is_default: bool | None = None
    status: Literal["active", "disabled"] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value


class ReviewFlowItem(BaseModel):
    id: str
    name: str
    description: str | None = None
    doc_type: str
    steps: list[ReviewStepItem]
    is_default: bool
    status: str
    created_at: datetime
    updated_at: datetime


class ReviewFlowListResponse(BaseModel):
    items: list[ReviewFlowItem]
    total: int


class ReviewSubmitRequest(BaseModel):
    document_id: str
    document_version_id: str | None = None
    flow_id: str | None = None
    comment: str | None = Field(default=None, max_length=1000)


class ReviewApproveRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)


class ReviewRejectRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=1000, description="驳回理由必填")

    @field_validator("comment")
    @classmethod
    def _strip(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("驳回理由不能为空")
        return value.strip()


class ReviewWithdrawRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=1000)


class ReviewRecordItem(BaseModel):
    id: str
    step_no: int
    step_name: str | None = None
    action: ReviewActionLiteral
    actor_id: str | None = None
    actor_name: str | None = None
    comment: str | None = None
    created_at: datetime


class ReviewInstanceItem(BaseModel):
    id: str
    document_id: str
    document_title: str
    doc_type: str
    version_no: int
    flow_id: str | None = None
    flow_name: str
    status: ReviewStatusLiteral
    current_step: int
    total_steps: int
    current_step_name: str | None = None
    submitted_by: str | None = None
    submitter_name: str | None = None
    submitted_at: datetime
    finished_at: datetime | None = None
    step_started_at: datetime | None = None
    waiting_workdays: int = 0
    overdue: bool = False


class ReviewInstanceDetail(ReviewInstanceItem):
    steps: list[ReviewStepItem] = Field(default_factory=list)
    records: list[ReviewRecordItem] = Field(default_factory=list)


class ReviewListResponse(BaseModel):
    items: list[ReviewInstanceItem]
    total: int
    page: int
    page_size: int


class ReviewQuery(BaseModel):
    scope: Literal["mine", "all"] = "mine"
    status: ReviewStatusLiteral | None = None
    doc_type: str | None = None
    submitted_by: str | None = None
    document_id: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
