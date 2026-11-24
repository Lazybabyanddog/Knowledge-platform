"""department 的查询封装：树遍历、唯一性与引用计数。"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.infra.db.models import AppUser, Department


class DepartmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, department_id: uuid.UUID) -> Department | None:
        return self.db.get(Department, department_id)

    def get_in_workspace(
        self, department_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> Department | None:
        return self.db.scalar(
            select(Department).where(
                Department.id == department_id,
                Department.workspace_id == workspace_id,
            )
        )

    def list_all(self, workspace_id: uuid.UUID) -> list[Department]:
        return list(
            self.db.scalars(
                select(Department)
                .where(Department.workspace_id == workspace_id)
                .order_by(Department.sort_order, Department.name)
            ).all()
        )

    def list_parent_pairs(self, workspace_id: uuid.UUID) -> list[tuple[uuid.UUID, uuid.UUID | None]]:
        rows = self.db.execute(
            select(Department.id, Department.parent_id).where(
                Department.workspace_id == workspace_id
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    def list_descendant_ids(
        self, workspace_id: uuid.UUID, root_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """返回 root 及其全部下级部门 id（部门量级小，内存 BFS 足够）。"""

        children: dict[uuid.UUID | None, list[uuid.UUID]] = {}
        for department_id, parent_id in self.list_parent_pairs(workspace_id):
            children.setdefault(parent_id, []).append(department_id)

        result: list[uuid.UUID] = []
        queue = [root_id]
        seen: set[uuid.UUID] = set()
        while queue:
            current = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            result.append(current)
            queue.extend(children.get(current, []))
        return result

    def find_by_name(
        self,
        workspace_id: uuid.UUID,
        parent_id: uuid.UUID | None,
        name: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> Department | None:
        conditions = [Department.workspace_id == workspace_id, Department.name == name]
        conditions.append(
            Department.parent_id.is_(None) if parent_id is None else Department.parent_id == parent_id
        )
        if exclude_id is not None:
            conditions.append(Department.id != exclude_id)
        return self.db.scalar(select(Department).where(*conditions))

    def count_children(self, department_id: uuid.UUID) -> int:
        return int(
            self.db.scalar(
                select(func.count()).select_from(Department).where(
                    Department.parent_id == department_id
                )
            )
            or 0
        )

    def count_users(self, department_id: uuid.UUID) -> int:
        return int(
            self.db.scalar(
                select(func.count()).select_from(AppUser).where(
                    AppUser.department_id == department_id
                )
            )
            or 0
        )

    def user_count_map(self, workspace_id: uuid.UUID) -> dict[uuid.UUID, int]:
        rows = self.db.execute(
            select(AppUser.department_id, func.count())
            .where(
                AppUser.workspace_id == workspace_id,
                AppUser.department_id.is_not(None),
            )
            .group_by(AppUser.department_id)
        ).all()
        return {department_id: int(count) for department_id, count in rows if department_id}

    def name_map(self, department_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not department_ids:
            return {}
        rows = self.db.execute(
            select(Department.id, Department.name).where(Department.id.in_(department_ids))
        ).all()
        return {department_id: name for department_id, name in rows}

    def add(self, department: Department) -> Department:
        self.db.add(department)
        return department

    def delete(self, department: Department) -> None:
        self.db.delete(department)
