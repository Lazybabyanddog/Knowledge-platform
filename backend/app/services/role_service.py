"""角色与权限目录服务：内置角色保护、删除前置检查与防锁死。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.domain.enums import AuditAction
from app.domain.policies import Permission
from app.infra.db.models import AppUser, Role
from app.infra.db.repositories import ReviewRepository, RoleRepository
from app.schemas.role import (
    PermissionCatalogResponse,
    PermissionGroup,
    PermissionItem,
    RoleCreateRequest,
    RoleItem,
    RoleUpdateRequest,
)
from app.services.audit_service import AuditService


def _to_item(role: Role, permissions: list[str], user_count: int) -> RoleItem:
    return RoleItem(
        id=str(role.id),
        code=role.code,
        name=role.name,
        description=role.description,
        is_system=bool(role.is_system),
        user_count=user_count,
        permissions=permissions,
    )


def _snapshot(role: Role, permissions: list[str]) -> dict[str, Any]:
    return {
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "is_system": bool(role.is_system),
        "permissions": sorted(permissions),
    }


class RoleService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.roles = RoleRepository(db)
        self.reviews = ReviewRepository(db)
        self.audit = AuditService(db)

    # ---- 查询 ----
    def list_roles(self) -> list[RoleItem]:
        counts = self.roles.user_count_map()
        return [
            _to_item(role, permissions, counts.get(role.id, 0))
            for role, permissions in self.roles.list_with_permissions()
        ]

    def permission_catalog(self) -> PermissionCatalogResponse:
        groups: dict[str, list[PermissionItem]] = {}
        for row in self.roles.list_permission_catalog():
            groups.setdefault(row.group_name, []).append(
                PermissionItem(code=row.code, name=row.name)
            )
        return PermissionCatalogResponse(
            groups=[PermissionGroup(group=group, items=items) for group, items in groups.items()]
        )

    # ---- 创建 ----
    def create_role(
        self,
        workspace_id: uuid.UUID,
        payload: RoleCreateRequest,
        *,
        operator: AppUser | None = None,
        ip: str | None = None,
    ) -> RoleItem:
        if self.roles.get_by_code(payload.code) is not None:
            raise ConflictError(f"角色编码 {payload.code} 已被占用")
        self._validate_permissions(payload.permissions)

        role = Role(
            code=payload.code,
            name=payload.name,
            description=payload.description,
            is_system=False,
        )
        self.roles.add(role)
        self.db.flush()
        self.roles.set_permissions(role.id, payload.permissions)
        self.db.flush()
        self._ensure_manageable()

        permissions = self.roles.permissions_of(role.id)
        self.audit.record(
            AuditAction.ROLE_CREATE,
            actor=operator,
            workspace_id=workspace_id,
            object_type="role",
            object_id=role.id,
            after=_snapshot(role, permissions),
            ip=ip,
        )
        self.db.commit()
        return _to_item(role, permissions, 0)

    # ---- 修改 ----
    def update_role(
        self,
        operator: AppUser,
        role_id: uuid.UUID,
        payload: RoleUpdateRequest,
        *,
        ip: str | None = None,
    ) -> RoleItem:
        role = self._get_or_404(role_id)
        before = _snapshot(role, self.roles.permissions_of(role.id))

        if payload.name is not None:
            role.name = payload.name
        if "description" in payload.model_fields_set:
            role.description = payload.description

        permissions_changed = payload.permissions is not None
        if permissions_changed:
            self._validate_permissions(payload.permissions or [])
            self.roles.set_permissions(role.id, payload.permissions or [])
            self.db.flush()
            self._ensure_manageable()

        permissions = self.roles.permissions_of(role.id)
        self.audit.record(
            AuditAction.ROLE_PERMISSION_UPDATE if permissions_changed else AuditAction.ROLE_UPDATE,
            actor=operator,
            workspace_id=operator.workspace_id,
            object_type="role",
            object_id=role.id,
            before=before,
            after=_snapshot(role, permissions),
            ip=ip,
        )
        self.db.commit()
        return _to_item(role, permissions, self.roles.count_users(role.id))

    # ---- 删除 ----
    def delete_role(
        self,
        operator: AppUser,
        role_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> None:
        role = self._get_or_404(role_id)
        if role.is_system:
            raise BadRequestError("内置角色不可删除")
        if self.roles.count_users(role.id) > 0:
            raise ConflictError("该角色仍被人员使用，请先调整这些人员的角色")
        if self._referenced_by_flows(operator.workspace_id, role.code):
            raise ConflictError("该角色仍被审批流步骤引用，请先调整审批流")

        snapshot = _snapshot(role, self.roles.permissions_of(role.id))
        self.roles.delete(role)
        self.db.flush()
        self._ensure_manageable()
        self.audit.record(
            AuditAction.ROLE_DELETE,
            actor=operator,
            workspace_id=operator.workspace_id,
            object_type="role",
            object_id=role_id,
            before=snapshot,
            ip=ip,
        )
        self.db.commit()

    # ---- 内部 ----
    def _get_or_404(self, role_id: uuid.UUID) -> Role:
        role = self.roles.get(role_id)
        if role is None:
            raise NotFoundError("角色不存在")
        return role

    def _validate_permissions(self, codes: list[str]) -> None:
        unknown = sorted(set(codes) - self.roles.known_permission_codes())
        if unknown:
            raise BadRequestError(f"未知权限: {', '.join(unknown)}")

    def _ensure_manageable(self) -> None:
        """至少保留一个"同时具备角色与用户管理、且有启用人员"的角色，避免锁死。"""

        counts = self.roles.active_user_count_map()
        required = {Permission.ROLE_MANAGE.value, Permission.USER_MANAGE.value}
        for role, permissions in self.roles.list_with_permissions():
            if required <= set(permissions) and counts.get(role.id, 0) > 0:
                return
        raise BadRequestError(
            "至少需要保留一个同时具备“角色权限管理”和“用户管理”权限、且有启用人员的角色"
        )

    def _referenced_by_flows(self, workspace_id: uuid.UUID, code: str) -> bool:
        for flow in self.reviews.list_flows(workspace_id, include_disabled=True):
            for step in flow.steps or []:
                if step.get("approver_type") == "role" and str(step.get("approver_value")) == code:
                    return True
        return False
