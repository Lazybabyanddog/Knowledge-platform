"""文档管理请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import DocumentStatus, Sensitivity

MAX_EXTRA_METADATA_KEYS = 50


class DocumentVersionItem(BaseModel):
    id: str
    document_id: str
    version_no: int
    file_name: str
    file_size: int
    mime_type: str
    checksum: str
    page_count: int | None = None
    is_current: bool
    change_note: str | None = None
    # 检索索引状态：pending / building / ready / failed / skipped
    index_status: str = "pending"
    index_error: str | None = None
    chunk_count: int = 0
    indexed_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime


class DocumentItem(BaseModel):
    id: str
    title: str
    summary: str | None = None
    source_type: str
    doc_type: str
    status: str
    current_version_id: str | None = None
    owner_id: str | None = None
    sensitivity: str
    tags: list[str] = Field(default_factory=list)
    extra_metadata: dict[str, Any] = Field(default_factory=dict)
    deleted_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    current_version: DocumentVersionItem | None = None


class DocumentListResponse(BaseModel):
    items: list[DocumentItem]
    total: int
    page: int
    page_size: int


class DocumentUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    summary: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=50)
    extra_metadata: dict[str, Any] | None = None
    sensitivity: Sensitivity | None = None
    status: DocumentStatus | None = None
    owner_id: str | None = None

    @field_validator("extra_metadata")
    @classmethod
    def _limit_extra_metadata(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if value is not None and len(value) > MAX_EXTRA_METADATA_KEYS:
            raise ValueError(f"扩展元数据最多 {MAX_EXTRA_METADATA_KEYS} 个键")
        return value

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = list(dict.fromkeys(tag.strip() for tag in value if tag and tag.strip()))
        too_long = [tag for tag in cleaned if len(tag) > 32]
        if too_long:
            raise ValueError("单个标签长度不得超过 32 个字符")
        return cleaned


class DocumentQuery(BaseModel):
    keyword: str | None = Field(default=None, max_length=128)
    status: DocumentStatus | None = None
    tag: str | None = Field(default=None, max_length=32)
    sensitivity: Sensitivity | None = None
    owner_id: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    sort_by: Literal["created_at", "updated_at"] = "created_at"
    order: Literal["asc", "desc"] = "desc"
    include_deleted: bool = False
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("keyword", "tag")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class DownloadUrl(BaseModel):
    url: str
    file_name: str
    version_no: int
    expires_in: int


class BatchUploadItem(BaseModel):
    file_name: str
    success: bool
    document_id: str | None = None
    version_id: str | None = None
    error: dict[str, Any] | None = None


class BatchUploadResponse(BaseModel):
    items: list[BatchUploadItem]
    succeeded: int
    failed: int
