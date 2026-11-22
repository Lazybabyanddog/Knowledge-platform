"""文档审批接口：审批流配置、提交、待办列表、审批动作与轨迹。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission, require_roles
from app.domain.enums import RoleCode
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.review import (
    ReviewApproveRequest,
    ReviewFlowCreateRequest,
    ReviewFlowItem,
    ReviewFlowListResponse,
    ReviewFlowUpdateRequest,
    ReviewInstanceDetail,
    ReviewInstanceItem,
    ReviewListResponse,
    ReviewQuery,
    ReviewRejectRequest,
    ReviewSubmitRequest,
    ReviewWithdrawRequest,
)
from app.services.review_service import ReviewService

DbSession = Annotated[Session, Depends(get_db)]
FlowReader = Annotated[
    Principal,
    Depends(require_permission(Permission.REVIEW_SUBMIT, Permission.REVIEW_APPROVE)),
]
FlowAdmin = Annotated[Principal, Depends(require_roles(RoleCode.ADMIN.value))]
SubmitPrincipal = Annotated[Principal, Depends(require_permission(Permission.REVIEW_SUBMIT))]
ApprovePrincipal = Annotated[Principal, Depends(require_permission(Permission.REVIEW_APPROVE))]
ViewPrincipal = Annotated[
    Principal,
    Depends(require_permission(Permission.REVIEW_SUBMIT, Permission.REVIEW_APPROVE)),
]

flows_router = APIRouter(prefix="/review-flows", tags=["审批流配置"])
reviews_router = APIRouter(prefix="/reviews", tags=["文档审批"])


# --------------------------------------------------------------------------- #
# 审批流配置
# --------------------------------------------------------------------------- #
@flows_router.get("", response_model=ReviewFlowListResponse, summary="审批流列表")
def list_flows(
    principal: FlowReader,
    db: DbSession,
    doc_type: Annotated[str | None, Query(description="按文档类型过滤")] = None,
    include_disabled: Annotated[bool, Query(description="是否包含已停用流程")] = False,
) -> ReviewFlowListResponse:
    return ReviewService(db).list_flows(
        principal, doc_type=doc_type, include_disabled=include_disabled
    )


@flows_router.post(
    "", response_model=ReviewFlowItem, status_code=201, summary="新建审批流（管理员）"
)
def create_flow(
    payload: ReviewFlowCreateRequest,
    principal: FlowAdmin,
    db: DbSession,
) -> ReviewFlowItem:
    return ReviewService(db).create_flow(principal, payload)


@flows_router.get("/{flow_id}", response_model=ReviewFlowItem, summary="审批流详情")
def get_flow(flow_id: uuid.UUID, principal: FlowReader, db: DbSession) -> ReviewFlowItem:
    return ReviewService(db).get_flow(principal, flow_id)


@flows_router.patch("/{flow_id}", response_model=ReviewFlowItem, summary="修改审批流（管理员）")
def update_flow(
    flow_id: uuid.UUID,
    payload: ReviewFlowUpdateRequest,
    principal: FlowAdmin,
    db: DbSession,
) -> ReviewFlowItem:
    return ReviewService(db).update_flow(principal, flow_id, payload)


# --------------------------------------------------------------------------- #
# 提交与审批动作
# --------------------------------------------------------------------------- #
@reviews_router.post(
    "/submit", response_model=ReviewInstanceItem, status_code=201, summary="提交文档审批"
)
def submit_review(
    payload: ReviewSubmitRequest,
    request: Request,
    principal: SubmitPrincipal,
    db: DbSession,
) -> ReviewInstanceItem:
    return ReviewService(db).submit(principal, payload, ip=client_ip(request))


@reviews_router.post(
    "/{instance_id}/approve", response_model=ReviewInstanceItem, summary="批准当前步骤"
)
def approve_review(
    instance_id: uuid.UUID,
    payload: ReviewApproveRequest,
    request: Request,
    principal: ApprovePrincipal,
    db: DbSession,
) -> ReviewInstanceItem:
    return ReviewService(db).approve(
        principal, instance_id, payload.comment, ip=client_ip(request)
    )


@reviews_router.post(
    "/{instance_id}/reject", response_model=ReviewInstanceItem, summary="驳回（理由必填）"
)
def reject_review(
    instance_id: uuid.UUID,
    payload: ReviewRejectRequest,
    request: Request,
    principal: ApprovePrincipal,
    db: DbSession,
) -> ReviewInstanceItem:
    return ReviewService(db).reject(principal, instance_id, payload, ip=client_ip(request))


@reviews_router.post(
    "/{instance_id}/withdraw", response_model=ReviewInstanceItem, summary="提交人撤回审批"
)
def withdraw_review(
    instance_id: uuid.UUID,
    payload: ReviewWithdrawRequest,
    request: Request,
    principal: SubmitPrincipal,
    db: DbSession,
) -> ReviewInstanceItem:
    return ReviewService(db).withdraw(
        principal, instance_id, payload.comment, ip=client_ip(request)
    )


# --------------------------------------------------------------------------- #
# 待办与详情
# --------------------------------------------------------------------------- #
@reviews_router.get("", response_model=ReviewListResponse, summary="审批列表（我的待办/全量）")
def list_reviews(
    principal: ViewPrincipal,
    db: DbSession,
    scope: Annotated[Literal["mine", "all"], Query()] = "mine",
    status: Annotated[
        Literal["pending", "approved", "rejected", "withdrawn"] | None, Query()
    ] = None,
    doc_type: Annotated[str | None, Query()] = None,
    submitted_by: Annotated[str | None, Query(description="提交人 UUID")] = None,
    document_id: Annotated[str | None, Query(description="按文档过滤")] = None,
    created_from: Annotated[datetime | None, Query()] = None,
    created_to: Annotated[datetime | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReviewListResponse:
    query = ReviewQuery(
        scope=scope,
        status=status,
        doc_type=doc_type,
        submitted_by=submitted_by,
        document_id=document_id,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
    )
    return ReviewService(db).list_instances(principal, query)


@reviews_router.get(
    "/{instance_id}", response_model=ReviewInstanceDetail, summary="审批详情与轨迹"
)
def get_review(
    instance_id: uuid.UUID, principal: ViewPrincipal, db: DbSession
) -> ReviewInstanceDetail:
    return ReviewService(db).get_detail(principal, instance_id)
