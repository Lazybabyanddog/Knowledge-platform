"""部门管理服务：树形校验、删除保护与审计。"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.domain.enums import AuditAction
from app.infra.db.models import AppUser, Department
from app.infra.db.repositories.department_repository import DepartmentRepository
from app.schemas.department import (
    DepartmentCreateRequest,
    DepartmentItem,
    DepartmentUpdateRequest,
)
from app.services.audit_service import AuditService

MAX_DEPTH = 5


def _to_item(department: Department, user_count: int) -> DepartmentItem:
    return DepartmentItem(
        id=str(department.id),
        name=department.name,
        parent_id=str(department.parent_id) if department.parent_id else None,
        sort_order=department.sort_order,
        user_count=user_count,
    )


def _snapshot(department: Department, parent_name: str | None) -> dict[str, object]:
    return {
        "name": department.name,
        "parent_id": str(department.parent_id) if department.parent_id else None,
        "parent_name": parent_name,
        "sort_order": department.sort_order,
    }


class DepartmentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.departments = DepartmentRepository(db)
        self.audit = AuditService(db)

    # ---- 查询 ----
    def list_departments(self, workspace_id: uuid.UUID) -> list[DepartmentItem]:
        departments = self.departments.list_all(workspace_id)
        counts = self.departments.user_count_map(workspace_id)
        return [
            _to_item(department, counts.get(department.id, 0)) for department in departments
        ]

    # ---- 创建 ----
    def create_department(
        self,
        workspace_id: uuid.UUID,
        payload: DepartmentCreateRequest,
        *,
        operator: AppUser | None = None,
        ip: str | None = None,
    ) -> DepartmentItem:
        parent_name = self._validate_parent(workspace_id, payload.parent_id)
        if payload.parent_id is not None:
            depth = self._depth_of(workspace_id, payload.parent_id) + 1
            if depth > MAX_DEPTH:
                raise BadRequestError(f"部门层级不能超过 {MAX_DEPTH} 层")
        if self.departments.find_by_name(workspace_id, payload.parent_id, payload.name):
            raise ConflictError("同级已存在同名部门")

        department = Department(
            workspace_id=workspace_id,
            parent_id=payload.parent_id,
            name=payload.name,
            sort_order=payload.sort_order,
        )
        self.departments.add(department)
        self.db.flush()
        self.audit.record(
            AuditAction.DEPARTMENT_CREATE,
            actor=operator,
            workspace_id=workspace_id,
            object_type="department",
            object_id=department.id,
            after=_snapshot(department, parent_name),
            ip=ip,
        )
        self.db.commit()
        return _to_item(department, 0)

    # ---- 修改 ----
    def update_department(
        self,
        operator: AppUser,
        department_id: uuid.UUID,
        payload: DepartmentUpdateRequest,
        *,
        ip: str | None = None,
    ) -> DepartmentItem:
        department = self._get_or_404(department_id)
        before = _snapshot(department, self._parent_name(department))

        target_parent_id = department.parent_id
        if "parent_id" in payload.model_fields_set:
            target_parent_id = payload.parent_id
            self._move(department, target_parent_id)

        if payload.name is not None:
            if self.departments.find_by_name(
                department.workspace_id,
                target_parent_id,
                payload.name,
                exclude_id=department.id,
            ):
                raise ConflictError("同级已存在同名部门")
            department.name = payload.name
        if payload.sort_order is not None:
            department.sort_order = payload.sort_order

        self.db.flush()
        self.audit.record(
            AuditAction.DEPARTMENT_UPDATE,
            actor=operator,
            workspace_id=department.workspace_id,
            object_type="department",
            object_id=department.id,
            before=before,
            after=_snapshot(department, self._parent_name(department)),
            ip=ip,
        )
        self.db.commit()
        return _to_item(department, self.departments.count_users(department.id))

    # ---- 删除 ----
    def delete_department(
        self,
        operator: AppUser,
        department_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> None:
        department = self._get_or_404(department_id)
        if self.departments.count_children(department.id) > 0:
            raise ConflictError("该部门下还有子部门，请先删除或移走子部门")
        if self.departments.count_users(department.id) > 0:
            raise ConflictError("该部门下还有人员，请先调整人员归属")

        snapshot = _snapshot(department, self._parent_name(department))
        self.departments.delete(department)
        self.db.flush()
        self.audit.record(
            AuditAction.DEPARTMENT_DELETE,
            actor=operator,
            workspace_id=department.workspace_id,
            object_type="department",
            object_id=department_id,
            before=snapshot,
            ip=ip,
        )
        self.db.commit()

    # ---- 内部 ----
    def _get_or_404(self, department_id: uuid.UUID) -> Department:
        department = self.departments.get(department_id)
        if department is None:
            raise NotFoundError("部门不存在")
        return department

    def _parent_name(self, department: Department) -> str | None:
        if department.parent_id is None:
            return None
        parent = self.departments.get(department.parent_id)
        return parent.name if parent else None

    def _validate_parent(
        self, workspace_id: uuid.UUID, parent_id: uuid.UUID | None
    ) -> str | None:
        if parent_id is None:
            return None
        parent = self.departments.get_in_workspace(parent_id, workspace_id)
        if parent is None:
            raise BadRequestError("上级部门不存在")
        return parent.name

    def _depth_of(self, workspace_id: uuid.UUID, department_id: uuid.UUID) -> int:
        parents = {child: parent for child, parent in self.departments.list_parent_pairs(workspace_id)}
        depth = 1
        current = parents.get(department_id)
        seen = {department_id}
        while current is not None and current not in seen:
            seen.add(current)
            depth += 1
            current = parents.get(current)
        return depth

    def _subtree_height(self, workspace_id: uuid.UUID, root_id: uuid.UUID) -> int:
        children: dict[uuid.UUID | None, list[uuid.UUID]] = {}
        for department_id, parent_id in self.departments.list_parent_pairs(workspace_id):
            children.setdefault(parent_id, []).append(department_id)

        height = 0
        queue: list[tuple[uuid.UUID, int]] = [(root_id, 1)]
        seen: set[uuid.UUID] = set()
        while queue:
            current, level = queue.pop()
            if current in seen:
                continue
            seen.add(current)
            height = max(height, level)
            for child in children.get(current, []):
                queue.append((child, level + 1))
        return height

    def _move(self, department: Department, target_parent_id: uuid.UUID | None) -> None:
        if target_parent_id == department.parent_id:
            return
        if target_parent_id is not None:
            if target_parent_id == department.id:
                raise BadRequestError("不能把部门移动到自身之下")
            self._validate_parent(department.workspace_id, target_parent_id)
            descendants = set(
                self.departments.list_descendant_ids(department.workspace_id, department.id)
            )
            if target_parent_id in descendants:
                raise BadRequestError("不能把部门移动到自己的下级部门之下")
            parent_depth = self._depth_of(department.workspace_id, target_parent_id)
            height = self._subtree_height(department.workspace_id, department.id)
            if parent_depth + height > MAX_DEPTH:
                raise BadRequestError(f"部门层级不能超过 {MAX_DEPTH} 层")
        department.parent_id = target_parent_id
