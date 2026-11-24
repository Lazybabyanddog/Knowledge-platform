"""角色与权限目录接口。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.role import (
    PermissionCatalogResponse,
    RoleCreateRequest,
    RoleItem,
    RoleListResponse,
    RoleUpdateRequest,
)
from app.services.role_service import RoleService

router = APIRouter(prefix="/roles", tags=["角色管理"])

DbSession = Annotated[Session, Depends(get_db)]
ManagePrincipal = Annotated[Principal, Depends(require_permission(Permission.ROLE_MANAGE))]


@router.get("", response_model=RoleListResponse, summary="角色列表")
def list_roles(principal: ManagePrincipal, db: DbSession) -> RoleListResponse:
    return RoleListResponse(items=RoleService(db).list_roles())


@router.get("/permissions", response_model=PermissionCatalogResponse, summary="权限目录")
def list_permissions(principal: ManagePrincipal, db: DbSession) -> PermissionCatalogResponse:
    return RoleService(db).permission_catalog()


@router.post("", response_model=RoleItem, status_code=201, summary="新建角色")
def create_role(
    payload: RoleCreateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> RoleItem:
    return RoleService(db).create_role(
        principal.user.workspace_id,
        payload,
        operator=principal.user,
        ip=client_ip(request),
    )


@router.patch("/{role_id}", response_model=RoleItem, summary="修改角色名称/描述/权限")
def update_role(
    role_id: uuid.UUID,
    payload: RoleUpdateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> RoleItem:
    return RoleService(db).update_role(
        principal.user, role_id, payload, ip=client_ip(request)
    )


@router.delete("/{role_id}", status_code=204, summary="删除角色")
def delete_role(
    role_id: uuid.UUID,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> None:
    RoleService(db).delete_role(principal.user, role_id, ip=client_ip(request))
