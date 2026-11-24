"""认证相关请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=8)


class LogoutRequest(BaseModel):
    """不传则吊销该用户全部刷新令牌，实现"全部设备下线"。"""

    refresh_token: str | None = None


class TokenUser(BaseModel):
    id: str
    display_name: str
    roles: list[str]


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: TokenUser


class MeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    email: str | None = None
    status: str
    workspace_id: str
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    last_login_at: datetime | None = None
