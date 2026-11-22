"""用户管理请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import UserStatus

_USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]{3,64}$"


class UserCreateRequest(BaseModel):
    username: str = Field(pattern=_USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    roles: list[str] = Field(default_factory=lambda: ["viewer"])


class UserUpdateRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    email: str | None = Field(default=None, max_length=255)
    status: UserStatus | None = None
    roles: list[str] | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class UserItem(BaseModel):
    id: str
    username: str
    display_name: str
    email: str | None = None
    status: str
    roles: list[str] = Field(default_factory=list)
    last_login_at: datetime | None = None
    created_at: datetime


class UserListResponse(BaseModel):
    items: list[UserItem]
    total: int
    page: int
    page_size: int


class UserQuery(BaseModel):
    keyword: str | None = Field(default=None, max_length=64)
    status: UserStatus | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @field_validator("keyword")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if value else value
