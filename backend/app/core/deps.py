"""FastAPI 依赖：当前用户与角色校验。"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.domain.enums import UserStatus
from app.domain.policies import Permission
from app.infra.db.models import AppUser
from app.infra.db.repositories import UserRepository
from app.infra.db.session import get_db

bearer_scheme = HTTPBearer(auto_error=False, description="Bearer 访问令牌")


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


@dataclass(frozen=True)
class Principal:
    """已认证主体：用户实体 + 实时角色与权限（不信任令牌内的快照）。"""

    user: AppUser
    roles: list[str]
    permissions: frozenset[str]

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def has_role(self, *codes: str) -> bool:
        return any(code in self.roles for code in codes)

    def has_permission(self, permission: Permission) -> bool:
        return permission.value in self.permissions


def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError()

    payload = decode_access_token(credentials.credentials)
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("令牌主题无效") from exc

    user = db.get(AppUser, user_id)
    if user is None:
        raise UnauthorizedError("用户不存在")
    if user.status != UserStatus.ACTIVE.value:
        raise ForbiddenError("账号已被禁用")

    roles, permissions = UserRepository(db).list_roles_and_permissions(user.id)
    return Principal(user=user, roles=roles, permissions=frozenset(permissions))


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_current_user(principal: CurrentPrincipal) -> AppUser:
    return principal.user


CurrentUser = Annotated[AppUser, Depends(get_current_user)]


def require_roles(*codes: str) -> Callable[[Principal], Principal]:
    """按角色编码精确放行，用于只有固定角色能进的接口。"""

    def _checker(principal: CurrentPrincipal) -> Principal:
        if not principal.has_role(*codes):
            raise ForbiddenError()
        return principal

    return _checker


def require_permission(*permissions: Permission) -> Callable[[Principal], Principal]:
    """按能力放行，多角色可共享同一能力；满足任意一个即通过。"""

    def _checker(principal: CurrentPrincipal) -> Principal:
        if not any(principal.has_permission(permission) for permission in permissions):
            raise ForbiddenError()
        return principal

    return _checker
