"""用户管理服务。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.domain.enums import AuditAction, UserStatus
from app.domain.policies import ROLE_PERMISSIONS
from app.infra.db.models import AppUser
from app.infra.db.repositories import RefreshTokenRepository, UserRepository
from app.schemas.user import (
    UserCreateRequest,
    UserItem,
    UserListResponse,
    UserQuery,
    UserUpdateRequest,
)
from app.services.audit_service import AuditService


def _to_item(user: AppUser, roles: list[str]) -> UserItem:
    return UserItem(
        id=str(user.id),
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        status=user.status,
        roles=roles,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _snapshot(user: AppUser, roles: list[str]) -> dict[str, Any]:
    """审计用的用户快照：只留可读字段，绝不包含密码哈希。"""
    return {
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status,
        "roles": sorted(roles),
    }


class UserService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.tokens = RefreshTokenRepository(db)
        self.audit = AuditService(db)

    # ---- 查询 ----
    def list_users(self, workspace_id: uuid.UUID, query: UserQuery) -> UserListResponse:
        users, total = self.users.list_paginated(
            workspace_id=workspace_id,
            keyword=query.keyword,
            status=query.status.value if query.status else None,
            page=query.page,
            page_size=query.page_size,
        )
        roles_map = self.users.list_roles_map([user.id for user in users])
        return UserListResponse(
            items=[_to_item(user, roles_map.get(user.id, [])) for user in users],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    # ---- 创建 ----
    def create_user(
        self,
        workspace_id: uuid.UUID,
        payload: UserCreateRequest,
        *,
        operator: AppUser | None = None,
        ip: str | None = None,
    ) -> UserItem:
        if self.users.get_by_username(payload.username) is not None:
            raise ConflictError(f"用户名 {payload.username} 已存在")

        role_ids = self._resolve_role_ids(payload.roles)

        user = AppUser(
            workspace_id=workspace_id,
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            status=UserStatus.ACTIVE.value,
        )
        self.db.add(user)
        self.db.flush()
        self.users.set_roles(user.id, role_ids)
        self.db.flush()  # 会话关闭了 autoflush，先落库再读回角色
        roles = self.users.list_roles(user.id)
        self.audit.record(
            AuditAction.USER_CREATE,
            actor=operator,
            workspace_id=workspace_id,
            object_type="user",
            object_id=user.id,
            after=_snapshot(user, roles),
            ip=ip,
        )
        self.db.commit()

        return _to_item(user, roles)

    # ---- 修改 ----
    def update_user(
        self,
        operator: AppUser,
        user_id: uuid.UUID,
        payload: UserUpdateRequest,
        *,
        ip: str | None = None,
    ) -> UserItem:
        user = self._get_or_404(user_id)

        if payload.status == UserStatus.DISABLED and user.id == operator.id:
            raise BadRequestError("不能禁用自己的账号")

        current_roles = self.users.list_roles(user.id)
        if payload.roles is not None and user.id == operator.id:
            if set(payload.roles) != set(current_roles):
                raise BadRequestError("不能修改自己的角色，请联系其他管理员")

        before = _snapshot(user, current_roles)

        if payload.display_name is not None:
            user.display_name = payload.display_name
        if payload.email is not None:
            user.email = payload.email
        if payload.status is not None:
            user.status = payload.status.value
            if payload.status == UserStatus.DISABLED:
                # 禁用即下线：吊销全部刷新令牌
                self.tokens.revoke_all_for_user(user.id)
        if payload.roles is not None:
            self.users.set_roles(user.id, self._resolve_role_ids(payload.roles))
            self.db.flush()

        roles = self.users.list_roles(user.id)
        self.audit.record(
            AuditAction.USER_UPDATE,
            actor=operator,
            workspace_id=user.workspace_id,
            object_type="user",
            object_id=user.id,
            before=before,
            after=_snapshot(user, roles),
            ip=ip,
        )
        self.db.commit()
        return _to_item(user, roles)

    # ---- 重置密码 ----
    def reset_password(
        self,
        operator: AppUser,
        user_id: uuid.UUID,
        new_password: str,
        *,
        ip: str | None = None,
    ) -> int:
        user = self._get_or_404(user_id)
        user.password_hash = hash_password(new_password)
        revoked = self.tokens.revoke_all_for_user(user.id)
        # 只记录"发生了重置"，不落任何凭据内容
        self.audit.record(
            AuditAction.USER_RESET_PASSWORD,
            actor=operator,
            workspace_id=user.workspace_id,
            object_type="user",
            object_id=user.id,
            after={"revoked_tokens": revoked},
            ip=ip,
        )
        self.db.commit()
        return revoked

    # ---- 内部 ----
    def _get_or_404(self, user_id: uuid.UUID) -> AppUser:
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError("用户不存在")
        return user

    def _resolve_role_ids(self, codes: list[str]) -> list[uuid.UUID]:
        unique = list(dict.fromkeys(codes))
        unknown = [code for code in unique if code not in ROLE_PERMISSIONS]
        if unknown:
            raise BadRequestError(f"未知角色: {', '.join(unknown)}")
        if not unique:
            raise BadRequestError("至少需要选择一个角色")
        role_ids = self.users.role_ids_by_codes(unique)
        if len(role_ids) != len(unique):
            raise BadRequestError("角色不存在")
        return role_ids
