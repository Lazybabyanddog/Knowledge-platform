"""抽取任务、实体、关系与子图的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.task import TaskActions, TaskErrorInfo, TaskProgress, TaskSkipReason

EntityStatusLiteral = Literal["proposed", "confirmed", "rejected", "merged"]
RelationStatusLiteral = Literal["proposed", "confirmed", "rejected"]


class ExtractionTaskCreateRequest(BaseModel):
    parse_task_id: str
    """可选：只抽取指定批次，调试用。"""

    limit_blocks: int | None = Field(default=None, ge=1, le=500)


class ExtractionRetryRequest(BaseModel):
    """人工复活。``degrade`` 为真时同时降低批大小，占用降参预算。"""

    degrade: bool = False


class ExtractionAttemptItem(BaseModel):
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


class ExtractionTaskItem(BaseModel):
    id: str
    document_id: str
    document_version_id: str
    parse_task_id: str
    ontology_version: int
    status: str
    attempt: int
    stats: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    error: TaskErrorInfo | None = None
    progress: TaskProgress = Field(default_factory=TaskProgress)
    auto_retries: int = 0
    downgrade_retries: int = 0
    manual_revives: int = 0
    # 下一次执行由谁排的：pending 时前端据此区分"系统重试中"与"人工触发"
    next_source: str | None = None
    incomplete: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class ExtractionTaskDetail(ExtractionTaskItem):
    attempts: list[ExtractionAttemptItem] = Field(default_factory=list)
    skip_summary: list[TaskSkipReason] = Field(default_factory=list)
    actions: TaskActions = Field(default_factory=TaskActions)


class ExtractionTaskQuery(BaseModel):
    document_id: str | None = None
    parse_task_id: str | None = None
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ExtractionTaskListResponse(BaseModel):
    items: list[ExtractionTaskItem]
    total: int
    page: int
    page_size: int


class EntityItem(BaseModel):
    id: str
    class_code: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    status: EntityStatusLiteral
    confidence: float
    merged_into: str | None = None
    reject_reason: str | None = None
    extraction_task_id: str | None = None
    document_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    evidence_block_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    identity_kind: str | None = None
    identity_key: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EntityQuery(BaseModel):
    status: EntityStatusLiteral | None = None
    class_code: str | None = None
    keyword: str | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    document_id: str | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class EntityListResponse(BaseModel):
    items: list[EntityItem]
    total: int
    page: int
    page_size: int


class EntityUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    attributes: dict[str, Any] | None = None


class EntityRejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class EntityMergeRequest(BaseModel):
    target_id: str
    source_ids: list[str] = Field(min_length=1, max_length=20)


class EntityMergeResponse(BaseModel):
    merge_id: str
    target_id: str
    source_ids: list[str]
    merged_count: int
    undo_deadline: datetime


class EntityMergeUndoRequest(BaseModel):
    target_id: str
    merge_id: str | None = None


class RelationItem(BaseModel):
    id: str
    code: str
    source_id: str
    target_id: str
    confidence: float
    status: RelationStatusLiteral
    evidence_block_id: str | None = None
    """支撑该关系实例的原文片段（模型抄写，已在写入前校验过属于该内容块）。"""
    evidence_text: str | None = None
    """证据键：文档+内容块+类型+两端+归一原文，同一证据只对应一条关系实例。"""
    evidence_key: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None
    document_id: str | None = None
    extraction_task_id: str | None = None
    reject_reason: str | None = None
    created_at: datetime | None = None


class RelationUpdateRequest(BaseModel):
    """只允许修属性与描述：关系的两端与类型是抽取事实，不应人工改写。"""

    attributes: dict[str, Any] | None = None
    description: str | None = Field(default=None, max_length=2000)


class RelationQuery(BaseModel):
    status: RelationStatusLiteral | None = None
    code: str | None = None
    document_id: str | None = None
    extraction_task_id: str | None = None
    min_confidence: float | None = Field(default=None, ge=0, le=1)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class RelationListResponse(BaseModel):
    items: list[RelationItem]
    total: int
    page: int
    page_size: int


class SubgraphEdge(BaseModel):
    id: str
    code: str
    source_id: str
    target_id: str
    confidence: float
    status: str


class SubgraphResponse(BaseModel):
    nodes: list[EntityItem]
    edges: list[SubgraphEdge]
    depth: int
    truncated: bool


class EntitySourceBlock(BaseModel):
    block_id: str
    document_id: str
    page_no: int | None = None
    snippet: str


class EntitySourceResponse(BaseModel):
    entity_id: str
    document_ids: list[str]
    blocks: list[EntitySourceBlock]
