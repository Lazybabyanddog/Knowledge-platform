"""本体的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

MAX_ATTRIBUTES = 30
_CODE_PATTERN = r"^[a-z][a-z0-9_]{1,63}$"


class OntologyClassCreateRequest(BaseModel):
    code: str = Field(pattern=_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: UUID | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes")
    @classmethod
    def _limit_attributes(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > MAX_ATTRIBUTES:
            raise ValueError(f"属性定义不得超过 {MAX_ATTRIBUTES} 个")
        return value


class OntologyClassUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    parent_id: UUID | None = None
    attributes: dict[str, Any] | None = None
    status: str | None = None

    @field_validator("attributes")
    @classmethod
    def _limit_attributes(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(value) > MAX_ATTRIBUTES:
            raise ValueError(f"属性定义不得超过 {MAX_ATTRIBUTES} 个")
        return value


class OntologyClassItem(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None
    parent_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    version: int
    status: str
    created_at: datetime
    updated_at: datetime


class OntologyRelationCreateRequest(BaseModel):
    code: str = Field(pattern=_CODE_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    source_class_id: UUID
    target_class_id: UUID


class OntologyRelationUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    source_class_id: UUID | None = None
    target_class_id: UUID | None = None
    status: str | None = None


class OntologyRelationItem(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None
    source_class_id: str
    target_class_id: str
    source_class_code: str
    target_class_code: str
    version: int
    status: str
    created_at: datetime
    updated_at: datetime


class OntologyClassListResponse(BaseModel):
    items: list[OntologyClassItem]
    total: int
    version: int


class OntologyRelationListResponse(BaseModel):
    items: list[OntologyRelationItem]
    total: int
    version: int
