"""部门管理接口：维护部门树，读取同时放行 user:manage（人员表单需要选项）。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.department import (
    DepartmentCreateRequest,
    DepartmentItem,
    DepartmentListResponse,
    DepartmentUpdateRequest,
)
from app.services.department_service import DepartmentService

router = APIRouter(prefix="/departments", tags=["部门管理"])

DbSession = Annotated[Session, Depends(get_db)]
ManagePrincipal = Annotated[
    Principal, Depends(require_permission(Permission.DEPARTMENT_MANAGE))
]
ReadablePrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.DEPARTMENT_MANAGE, Permission.USER_MANAGE)),
]


@router.get("", response_model=DepartmentListResponse, summary="部门列表（扁平，前端组树）")
def list_departments(principal: ReadablePrincipal, db: DbSession) -> DepartmentListResponse:
    items = DepartmentService(db).list_departments(principal.user.workspace_id)
    return DepartmentListResponse(items=items)


@router.post("", response_model=DepartmentItem, status_code=201, summary="新建部门")
def create_department(
    payload: DepartmentCreateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> DepartmentItem:
    return DepartmentService(db).create_department(
        principal.user.workspace_id,
        payload,
        operator=principal.user,
        ip=client_ip(request),
    )


@router.patch("/{department_id}", response_model=DepartmentItem, summary="修改/移动部门")
def update_department(
    department_id: uuid.UUID,
    payload: DepartmentUpdateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> DepartmentItem:
    return DepartmentService(db).update_department(
        principal.user, department_id, payload, ip=client_ip(request)
    )


@router.delete("/{department_id}", status_code=204, summary="删除部门")
def delete_department(
    department_id: uuid.UUID,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> None:
    DepartmentService(db).delete_department(
        principal.user, department_id, ip=client_ip(request)
    )
