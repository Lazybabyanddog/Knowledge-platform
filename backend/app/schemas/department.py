"""部门管理请求/响应模型。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator


class DepartmentCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    parent_id: uuid.UUID | None = None
    sort_order: int = Field(default=0, ge=0, le=9999)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("部门名称不能为空")
        return stripped


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: uuid.UUID | None = None
    sort_order: int | None = Field(default=None, ge=0, le=9999)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("部门名称不能为空")
        return stripped


class DepartmentItem(BaseModel):
    id: str
    name: str
    parent_id: str | None = None
    sort_order: int = 0
    user_count: int = 0


class DepartmentListResponse(BaseModel):
    items: list[DepartmentItem]
