"""抽取任务、实体、关系与子图的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EntityStatusLiteral = Literal["proposed", "confirmed", "rejected", "merged"]
RelationStatusLiteral = Literal["proposed", "confirmed", "rejected"]


class ExtractionTaskCreateRequest(BaseModel):
    parse_task_id: str
    """可选：只抽取指定批次，调试用。"""

    limit_blocks: int | None = Field(default=None, ge=1, le=500)


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
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


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
    document_id: str | None = None
    extraction_task_id: str | None = None
    reject_reason: str | None = None
    created_at: datetime | None = None


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
