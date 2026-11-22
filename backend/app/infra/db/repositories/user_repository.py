"""app_user / user_role 的查询封装。"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.infra.db.models import AppUser, Role, UserRole


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: uuid.UUID) -> AppUser | None:
        return self.db.get(AppUser, user_id)

    def get_by_username(self, username: str) -> AppUser | None:
        return self.db.scalar(select(AppUser).where(AppUser.username == username))

    def list_roles(self, user_id: uuid.UUID) -> list[str]:
        rows = self.db.execute(
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
            .order_by(Role.code)
        ).all()
        return [code for (code,) in rows]

    def list_roles_map(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not user_ids:
            return {}
        rows = self.db.execute(
            select(UserRole.user_id, Role.code)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids))
            .order_by(Role.code)
        ).all()
        mapping: dict[uuid.UUID, list[str]] = {user_id: [] for user_id in user_ids}
        for user_id, code in rows:
            mapping[user_id].append(code)
        return mapping

    def list_paginated(
        self,
        *,
        workspace_id: uuid.UUID,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AppUser], int]:
        conditions = [AppUser.workspace_id == workspace_id]
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    AppUser.username.ilike(pattern),
                    AppUser.display_name.ilike(pattern),
                    AppUser.email.ilike(pattern),
                )
            )
        if status:
            conditions.append(AppUser.status == status)

        total = self.db.scalar(select(func.count()).select_from(AppUser).where(*conditions))
        rows = self.db.scalars(
            select(AppUser)
            .where(*conditions)
            .order_by(AppUser.created_at.desc(), AppUser.username)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return list(rows), int(total or 0)

    def set_roles(self, user_id: uuid.UUID, role_ids: list[uuid.UUID]) -> None:
        self.db.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role_id in role_ids:
            self.db.add(UserRole(user_id=user_id, role_id=role_id))

    def role_ids_by_codes(self, codes: list[str]) -> list[uuid.UUID]:
        if not codes:
            return []
        rows = self.db.scalars(select(Role.id).where(Role.code.in_(codes))).all()
        return list(rows)
