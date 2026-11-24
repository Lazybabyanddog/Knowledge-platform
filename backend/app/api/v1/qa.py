"""问答反馈与低置信度治理接口。

两条权限线：

- ``/qa-records/*``：任何登录用户，只能碰本人发起的问答；
- ``/qa-issues/*``：需要 ``qa:manage``，且读写都按空间与密级过滤。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentPrincipal, Principal, client_ip, require_permission
from app.domain.policies import Permission
from app.infra.db.models.qa import QaFeedback
from app.infra.db.session import get_db
from app.schemas.qa import (
    QaAssignRequest,
    QaAssigneeItem,
    QaCloseRequest,
    QaDisposeRequest,
    QaFeedbackItem,
    QaFeedbackRequest,
    QaFeedbackResponse,
    QaIssueDetail,
    QaIssueListResponse,
    QaReopenRequest,
)
from app.services.qa_service import QaService, QaTriageService

DbSession = Annotated[Session, Depends(get_db)]
QaManager = Annotated[Principal, Depends(require_permission(Permission.QA_MANAGE))]

records_router = APIRouter(prefix="/qa-records", tags=["问答反馈"])
issues_router = APIRouter(prefix="/qa-issues", tags=["问答治理"])


def _feedback_item(feedback: QaFeedback | None) -> QaFeedbackItem | None:
    if feedback is None:
        return None
    return QaFeedbackItem(
        id=str(feedback.id),
        record_id=str(feedback.record_id),
        rating=feedback.rating,
        reason=feedback.reason,
        note=feedback.note,
        created_at=feedback.created_at,
        updated_at=feedback.updated_at,
    )


@records_router.put(
    "/{record_id}/feedback",
    response_model=QaFeedbackResponse,
    summary="提交或修改本人对一次问答的评价",
)
def submit_feedback(
    record_id: uuid.UUID,
    payload: QaFeedbackRequest,
    request: Request,
    principal: CurrentPrincipal,
    db: DbSession,
) -> QaFeedbackResponse:
    feedback = QaService(db).submit_feedback(
        principal,
        record_id,
        rating=payload.rating,
        reason=payload.reason,
        note=payload.note,
        ip=client_ip(request),
    )
    db.commit()
    return QaFeedbackResponse(feedback=_feedback_item(feedback))


@records_router.get(
    "/{record_id}/feedback",
    response_model=QaFeedbackResponse,
    summary="读回本人已提交的评价",
)
def get_feedback(
    record_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession
) -> QaFeedbackResponse:
    feedback = QaService(db).get_feedback(principal, record_id)
    return QaFeedbackResponse(feedback=_feedback_item(feedback))


@issues_router.get("/assignable-users", response_model=list[QaAssigneeItem], summary="可指派人列表")
def list_assignees(principal: QaManager, db: DbSession) -> list[QaAssigneeItem]:
    return [
        QaAssigneeItem(id=str(user_id), username=username, display_name=display_name)
        for user_id, username, display_name in QaTriageService(db).list_assignees(principal)
    ]


@issues_router.get("", response_model=QaIssueListResponse, summary="低置信度问题簇列表")
def list_issues(
    principal: QaManager,
    db: DbSession,
    status: Annotated[str | None, Query(description="pending / closed")] = None,
    disposition: Annotated[str | None, Query(description="处置结论")] = None,
    reason: Annotated[str | None, Query(description="触发原因")] = None,
    assignee_id: Annotated[uuid.UUID | None, Query(description="处理人")] = None,
    document_id: Annotated[uuid.UUID | None, Query(description="关联文档")] = None,
    created_from: Annotated[datetime | None, Query(description="最近发生时间下界")] = None,
    created_to: Annotated[datetime | None, Query(description="最近发生时间上界")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> QaIssueListResponse:
    items, total = QaTriageService(db).list_issues(
        principal,
        page=page,
        page_size=page_size,
        status=status,
        disposition=disposition,
        reason=reason,
        assignee_id=assignee_id,
        document_id=document_id,
        created_from=created_from,
        created_to=created_to,
    )
    return QaIssueListResponse(items=items, total=total, page=page, page_size=page_size)


@issues_router.get("/{issue_id}", response_model=QaIssueDetail, summary="问题簇详情")
def get_issue(issue_id: uuid.UUID, principal: QaManager, db: DbSession) -> QaIssueDetail:
    return QaIssueDetail(**QaTriageService(db).get_detail(principal, issue_id))


@issues_router.post("/{issue_id}/assign", response_model=QaIssueDetail, summary="指派处理人")
def assign_issue(
    issue_id: uuid.UUID,
    payload: QaAssignRequest,
    request: Request,
    principal: QaManager,
    db: DbSession,
) -> QaIssueDetail:
    assignee_id = uuid.UUID(payload.assignee_id) if payload.assignee_id else None
    QaTriageService(db).assign(
        principal, issue_id, assignee_id, payload.note, ip=client_ip(request)
    )
    db.commit()
    return QaIssueDetail(**QaTriageService(db).get_detail(principal, issue_id))


@issues_router.post("/{issue_id}/dispose", response_model=QaIssueDetail, summary="设置处置结论")
def dispose_issue(
    issue_id: uuid.UUID,
    payload: QaDisposeRequest,
    request: Request,
    principal: QaManager,
    db: DbSession,
) -> QaIssueDetail:
    QaTriageService(db).dispose(
        principal, issue_id, payload.disposition, payload.note, ip=client_ip(request)
    )
    db.commit()
    return QaIssueDetail(**QaTriageService(db).get_detail(principal, issue_id))


@issues_router.post("/{issue_id}/close", response_model=QaIssueDetail, summary="关闭问题簇")
def close_issue(
    issue_id: uuid.UUID,
    payload: QaCloseRequest,
    request: Request,
    principal: QaManager,
    db: DbSession,
) -> QaIssueDetail:
    QaTriageService(db).close(
        principal, issue_id, payload.disposition, payload.note, ip=client_ip(request)
    )
    db.commit()
    return QaIssueDetail(**QaTriageService(db).get_detail(principal, issue_id))


@issues_router.post("/{issue_id}/reopen", response_model=QaIssueDetail, summary="重开问题簇")
def reopen_issue(
    issue_id: uuid.UUID,
    payload: QaReopenRequest,
    request: Request,
    principal: QaManager,
    db: DbSession,
) -> QaIssueDetail:
    QaTriageService(db).reopen(principal, issue_id, payload.note, ip=client_ip(request))
    db.commit()
    return QaIssueDetail(**QaTriageService(db).get_detail(principal, issue_id))


__all__ = ["issues_router", "records_router"]
