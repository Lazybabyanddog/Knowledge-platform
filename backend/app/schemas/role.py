"""角色与权限目录的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

ROLE_CODE_PATTERN = r"^[a-z][a-z0-9_-]{1,31}$"


class RoleItem(BaseModel):
    id: str
    code: str
    name: str
    description: str | None = None
    is_system: bool = False
    user_count: int = 0
    permissions: list[str] = Field(default_factory=list)


class RoleListResponse(BaseModel):
    items: list[RoleItem]


class RoleCreateRequest(BaseModel):
    code: str = Field(pattern=ROLE_CODE_PATTERN, min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("角色名称不能为空")
        return stripped


class RoleUpdateRequest(BaseModel):
    """code 不可修改：它会被审批流与令牌引用。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("角色名称不能为空")
        return stripped


class PermissionItem(BaseModel):
    code: str
    name: str


class PermissionGroup(BaseModel):
    group: str
    items: list[PermissionItem] = Field(default_factory=list)


class PermissionCatalogResponse(BaseModel):
    groups: list[PermissionGroup]
