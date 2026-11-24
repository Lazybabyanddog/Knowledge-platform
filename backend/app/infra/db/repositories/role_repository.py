"""role / role_permission / permission 的查询封装。"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.domain.enums import UserStatus
from app.infra.db.models import AppUser, Permission, Role, RolePermission, UserRole


class RoleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, role_id: uuid.UUID) -> Role | None:
        return self.db.get(Role, role_id)

    def get_by_code(self, code: str) -> Role | None:
        return self.db.scalar(select(Role).where(Role.code == code))

    def list_all(self) -> list[Role]:
        return list(self.db.scalars(select(Role).order_by(Role.code)).all())

    def list_with_permissions(self) -> list[tuple[Role, list[str]]]:
        rows = self.db.execute(
            select(Role, RolePermission.permission_code)
            .outerjoin(RolePermission, RolePermission.role_id == Role.id)
            .order_by(Role.code, RolePermission.permission_code)
        ).all()
        merged: dict[uuid.UUID, tuple[Role, list[str]]] = {}
        for role, permission_code in rows:
            entry = merged.setdefault(role.id, (role, []))
            if permission_code is not None:
                entry[1].append(permission_code)
        return list(merged.values())

    def permissions_of(self, role_id: uuid.UUID) -> list[str]:
        rows = self.db.scalars(
            select(RolePermission.permission_code)
            .where(RolePermission.role_id == role_id)
            .order_by(RolePermission.permission_code)
        ).all()
        return list(rows)

    def permissions_map(self, role_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not role_ids:
            return {}
        rows = self.db.execute(
            select(RolePermission.role_id, RolePermission.permission_code)
            .where(RolePermission.role_id.in_(role_ids))
            .order_by(RolePermission.permission_code)
        ).all()
        mapping: dict[uuid.UUID, list[str]] = {role_id: [] for role_id in role_ids}
        for role_id, code in rows:
            mapping[role_id].append(code)
        return mapping

    def set_permissions(self, role_id: uuid.UUID, codes: list[str]) -> None:
        self.db.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        for code in dict.fromkeys(codes):
            self.db.add(RolePermission(role_id=role_id, permission_code=code))

    def list_permission_catalog(self) -> list[Permission]:
        return list(
            self.db.scalars(
                select(Permission).order_by(Permission.sort_order, Permission.code)
            ).all()
        )

    def known_permission_codes(self) -> set[str]:
        return set(self.db.scalars(select(Permission.code)).all())

    def exists_codes(self, codes: list[str]) -> set[str]:
        if not codes:
            return set()
        return set(self.db.scalars(select(Role.code).where(Role.code.in_(codes))).all())

    def add(self, role: Role) -> Role:
        self.db.add(role)
        return role

    def delete(self, role: Role) -> None:
        self.db.delete(role)

    def user_count_map(self) -> dict[uuid.UUID, int]:
        rows = self.db.execute(
            select(UserRole.role_id, func.count()).group_by(UserRole.role_id)
        ).all()
        return {role_id: int(count) for role_id, count in rows}

    def active_user_count_map(self) -> dict[uuid.UUID, int]:
        rows = self.db.execute(
            select(UserRole.role_id, func.count())
            .join(AppUser, AppUser.id == UserRole.user_id)
            .where(AppUser.status == UserStatus.ACTIVE.value)
            .group_by(UserRole.role_id)
        ).all()
        return {role_id: int(count) for role_id, count in rows}

    def count_users(self, role_id: uuid.UUID) -> int:
        return int(
            self.db.scalar(
                select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)
            )
            or 0
        )
