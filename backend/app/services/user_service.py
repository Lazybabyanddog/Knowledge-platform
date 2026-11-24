"""用户管理服务。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.security import hash_password
from app.domain.enums import AuditAction, UserStatus
from app.infra.db.models import AppUser, Department
from app.infra.db.repositories import (
    DepartmentRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.schemas.user import (
    UserCreateRequest,
    UserItem,
    UserListResponse,
    UserQuery,
    UserUpdateRequest,
)
from app.services.audit_service import AuditService


def _to_item(user: AppUser, roles: list[str], department_name: str | None = None) -> UserItem:
    return UserItem(
        id=str(user.id),
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        status=user.status,
        roles=roles,
        department_id=str(user.department_id) if user.department_id else None,
        department_name=department_name,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _snapshot(
    user: AppUser, roles: list[str], department_name: str | None = None
) -> dict[str, Any]:
    """审计用的用户快照：只留可读字段，绝不包含密码哈希。"""
    return {
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "status": user.status,
        "roles": sorted(roles),
        "department_name": department_name,
    }


class UserService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.departments = DepartmentRepository(db)
        self.tokens = RefreshTokenRepository(db)
        self.audit = AuditService(db)

    # ---- 查询 ----
    def list_users(self, workspace_id: uuid.UUID, query: UserQuery) -> UserListResponse:
        department_ids: list[uuid.UUID] | None = None
        if query.department_id is not None:
            department = self.departments.get_in_workspace(query.department_id, workspace_id)
            if department is None:
                raise BadRequestError("部门不存在")
            department_ids = self.departments.list_descendant_ids(workspace_id, department.id)

        users, total = self.users.list_paginated(
            workspace_id=workspace_id,
            keyword=query.keyword,
            status=query.status.value if query.status else None,
            department_ids=department_ids,
            page=query.page,
            page_size=query.page_size,
        )
        roles_map = self.users.list_roles_map([user.id for user in users])
        names = self.departments.name_map(
            [user.department_id for user in users if user.department_id is not None]
        )
        return UserListResponse(
            items=[
                _to_item(
                    user,
                    roles_map.get(user.id, []),
                    names.get(user.department_id) if user.department_id else None,
                )
                for user in users
            ],
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
        department = self._resolve_department(workspace_id, payload.department_id)

        user = AppUser(
            workspace_id=workspace_id,
            username=payload.username,
            email=payload.email,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            department_id=department.id if department else None,
            status=UserStatus.ACTIVE.value,
        )
        self.db.add(user)
        self.db.flush()
        self.users.set_roles(user.id, role_ids)
        self.db.flush()  # 会话关闭了 autoflush，先落库再读回角色
        roles = self.users.list_roles(user.id)
        department_name = department.name if department else None
        self.audit.record(
            AuditAction.USER_CREATE,
            actor=operator,
            workspace_id=workspace_id,
            object_type="user",
            object_id=user.id,
            after=_snapshot(user, roles, department_name),
            ip=ip,
        )
        self.db.commit()

        return _to_item(user, roles, department_name)

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

        current_department_name = self._department_name(user.department_id)
        before = _snapshot(user, current_roles, current_department_name)

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
        if "department_id" in payload.model_fields_set:
            department = self._resolve_department(user.workspace_id, payload.department_id)
            user.department_id = department.id if department else None

        roles = self.users.list_roles(user.id)
        department_name = self._department_name(user.department_id)
        self.audit.record(
            AuditAction.USER_UPDATE,
            actor=operator,
            workspace_id=user.workspace_id,
            object_type="user",
            object_id=user.id,
            before=before,
            after=_snapshot(user, roles, department_name),
            ip=ip,
        )
        self.db.commit()
        return _to_item(user, roles, department_name)

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

    def _department_name(self, department_id: uuid.UUID | None) -> str | None:
        if department_id is None:
            return None
        department = self.departments.get(department_id)
        return department.name if department else None

    def _resolve_department(
        self, workspace_id: uuid.UUID, department_id: uuid.UUID | None
    ) -> Department | None:
        if department_id is None:
            return None
        department = self.departments.get_in_workspace(department_id, workspace_id)
        if department is None:
            raise BadRequestError("部门不存在")
        return department

    def _resolve_role_ids(self, codes: list[str]) -> list[uuid.UUID]:
        unique = list(dict.fromkeys(codes))
        known = set(self.users.list_role_codes())
        unknown = [code for code in unique if code not in known]
        if unknown:
            raise BadRequestError(f"未知角色: {', '.join(unknown)}")
        if not unique:
            raise BadRequestError("至少需要选择一个角色")
        role_ids = self.users.role_ids_by_codes(unique)
        if len(role_ids) != len(unique):
            raise BadRequestError("角色不存在")
        return role_ids
