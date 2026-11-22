"""本体管理接口。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.ontology import (
    OntologyClassCreateRequest,
    OntologyClassItem,
    OntologyClassListResponse,
    OntologyClassUpdateRequest,
    OntologyRelationCreateRequest,
    OntologyRelationItem,
    OntologyRelationListResponse,
    OntologyRelationUpdateRequest,
)
from app.services.ontology_service import OntologyService

DbSession = Annotated[Session, Depends(get_db)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.GRAPH_READ))]
ManagePrincipal = Annotated[Principal, Depends(require_permission(Permission.ONTOLOGY_MANAGE))]

router = APIRouter(prefix="/ontology", tags=["本体管理"])


@router.get("/classes", response_model=OntologyClassListResponse, summary="实体类列表")
def list_classes(
    principal: ReadPrincipal,
    db: DbSession,
    include_disabled: Annotated[bool, Query()] = False,
) -> OntologyClassListResponse:
    return OntologyService(db).list_classes(principal, include_disabled=include_disabled)


@router.post(
    "/classes", response_model=OntologyClassItem, status_code=201, summary="新建实体类"
)
def create_class(
    payload: OntologyClassCreateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> OntologyClassItem:
    return OntologyService(db).create_class(principal, payload, ip=client_ip(request))


@router.patch(
    "/classes/{class_id}", response_model=OntologyClassItem, summary="修改实体类"
)
def update_class(
    class_id: uuid.UUID,
    payload: OntologyClassUpdateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> OntologyClassItem:
    return OntologyService(db).update_class(principal, class_id, payload, ip=client_ip(request))


@router.get("/relations", response_model=OntologyRelationListResponse, summary="关系类型列表")
def list_relations(
    principal: ReadPrincipal,
    db: DbSession,
    include_disabled: Annotated[bool, Query()] = False,
) -> OntologyRelationListResponse:
    return OntologyService(db).list_relations(principal, include_disabled=include_disabled)


@router.post(
    "/relations", response_model=OntologyRelationItem, status_code=201, summary="新建关系类型"
)
def create_relation(
    payload: OntologyRelationCreateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> OntologyRelationItem:
    return OntologyService(db).create_relation(principal, payload, ip=client_ip(request))


@router.patch(
    "/relations/{relation_id}", response_model=OntologyRelationItem, summary="修改关系类型"
)
def update_relation(
    relation_id: uuid.UUID,
    payload: OntologyRelationUpdateRequest,
    request: Request,
    principal: ManagePrincipal,
    db: DbSession,
) -> OntologyRelationItem:
    return OntologyService(db).update_relation(
        principal, relation_id, payload, ip=client_ip(request)
    )


@router.post("/sync", summary="重放本体到图库")
def sync_ontology(principal: ManagePrincipal, db: DbSession) -> dict[str, int]:
    count = OntologyService(db).resync(principal)
    return {"projected": count}
