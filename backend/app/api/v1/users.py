"""用户管理接口：仅具备 user:manage 能力的角色可访问。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.user import (
    ResetPasswordRequest,
    UserCreateRequest,
    UserItem,
    UserListResponse,
    UserQuery,
    UserUpdateRequest,
)
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["用户管理"])

DbSession = Annotated[Session, Depends(get_db)]
ManagePrincipal = Annotated[Principal, Depends(require_permission(Permission.USER_MANAGE))]


@router.get("", response_model=UserListResponse, summary="用户列表")
def list_users(
    query: Annotated[UserQuery, Query()],
    principal: ManagePrincipal,
    db: DbSession,
) -> UserListResponse:
    return UserService(db).list_users(principal.user.workspace_id, query)


@router.post("", response_model=UserItem, status_code=201, summary="创建用户")
def create_user(
    payload: UserCreateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> UserItem:
    return UserService(db).create_user(
        principal.user.workspace_id,
        payload,
        operator=principal.user,
        ip=client_ip(request),
    )


@router.patch("/{user_id}", response_model=UserItem, summary="修改用户")
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> UserItem:
    return UserService(db).update_user(
        principal.user, user_id, payload, ip=client_ip(request)
    )


@router.post("/{user_id}/reset-password", summary="重置用户密码")
def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> dict[str, int]:
    revoked = UserService(db).reset_password(
        principal.user, user_id, payload.new_password, ip=client_ip(request)
    )
    return {"revoked_tokens": revoked}
