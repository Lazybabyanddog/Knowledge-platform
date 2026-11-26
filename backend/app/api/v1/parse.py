"""解析策略与解析任务接口。"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission, require_roles
from app.domain.enums import RoleCode
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.parse import (
    ParseBlockListResponse,
    ParseEngineListResponse,
    ParseStrategyCreateRequest,
    ParseStrategyItem,
    ParseStrategyListResponse,
    ParseStrategyUpdateRequest,
    ParseTaskCreateRequest,
    ParseTaskDetail,
    ParseTaskItem,
    ParseTaskListResponse,
    ParseTaskQuery,
)
from app.services.parse_strategy_service import ParseStrategyService
from app.services.parse_task_service import ParseTaskService, task_item

DbSession = Annotated[Session, Depends(get_db)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.DOCUMENT_READ))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.DOCUMENT_WRITE))]
AdminPrincipal = Annotated[Principal, Depends(require_roles(RoleCode.ADMIN.value))]

strategies_router = APIRouter(prefix="/parse-strategies", tags=["解析策略"])
tasks_router = APIRouter(prefix="/parse-tasks", tags=["解析任务"])


@strategies_router.get("", response_model=ParseStrategyListResponse, summary="策略列表")
def list_strategies(
    principal: ReadPrincipal,
    db: DbSession,
    doc_type: Annotated[str | None, Query(pattern="^(pdf|office|image|text|html)$")] = None,
) -> ParseStrategyListResponse:
    items = ParseStrategyService(db).list_strategies(principal, doc_type)
    return ParseStrategyListResponse(items=items)


@strategies_router.get(
    "/engines", response_model=ParseEngineListResponse, summary="可用解析引擎"
)
def list_engines(_principal: ReadPrincipal, db: DbSession) -> ParseEngineListResponse:
    # 必须声明在 /{strategy_id} 之前，否则 engines 会被当成 id 解析
    return ParseEngineListResponse(items=ParseStrategyService(db).list_engines())


@strategies_router.get(
    "/{strategy_id}", response_model=ParseStrategyItem, summary="策略详情"
)
def get_strategy(
    strategy_id: uuid.UUID, principal: ReadPrincipal, db: DbSession
) -> ParseStrategyItem:
    return ParseStrategyService(db).get_strategy(principal, strategy_id)


@strategies_router.post("", response_model=ParseStrategyItem, status_code=201, summary="新建策略")
def create_strategy(
    payload: ParseStrategyCreateRequest,
    request: Request,
    principal: AdminPrincipal,
    db: DbSession,
) -> ParseStrategyItem:
    return ParseStrategyService(db).create_strategy(principal, payload, ip=client_ip(request))


@strategies_router.patch(
    "/{strategy_id}", response_model=ParseStrategyItem, summary="修改策略（生成新版本）"
)
def update_strategy(
    strategy_id: uuid.UUID,
    payload: ParseStrategyUpdateRequest,
    request: Request,
    principal: AdminPrincipal,
    db: DbSession,
) -> ParseStrategyItem:
    return ParseStrategyService(db).update_strategy(
        principal, strategy_id, payload, ip=client_ip(request)
    )


@strategies_router.delete("/{strategy_id}", summary="删除策略（停用全部版本）")
def delete_strategy(
    strategy_id: uuid.UUID,
    request: Request,
    principal: AdminPrincipal,
    db: DbSession,
) -> dict[str, object]:
    disabled = ParseStrategyService(db).delete_strategy(
        principal, strategy_id, ip=client_ip(request)
    )
    return {"deleted": True, "disabled_versions": disabled}


@tasks_router.post("", response_model=ParseTaskItem, status_code=202, summary="触发解析")
def trigger_parse(
    payload: ParseTaskCreateRequest,
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
) -> ParseTaskItem:
    task = ParseTaskService(db).trigger(principal, payload, ip=client_ip(request))
    return task_item(task)


@tasks_router.get("", response_model=ParseTaskListResponse, summary="解析任务列表")
def list_parse_tasks(
    query: Annotated[ParseTaskQuery, Query()],
    principal: ReadPrincipal,
    db: DbSession,
) -> ParseTaskListResponse:
    return ParseTaskService(db).list_tasks(principal, query)


@tasks_router.get("/{task_id}", response_model=ParseTaskDetail, summary="解析任务详情")
def get_parse_task(
    task_id: uuid.UUID, principal: ReadPrincipal, db: DbSession
) -> ParseTaskDetail:
    return ParseTaskService(db).get_detail(principal, task_id)


@tasks_router.post("/{task_id}/retry", response_model=ParseTaskItem, summary="重试解析")
def retry_parse_task(
    task_id: uuid.UUID,
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
) -> ParseTaskItem:
    task = ParseTaskService(db).retry(principal, task_id, ip=client_ip(request))
    return task_item(task)


@tasks_router.post("/{task_id}/cancel", response_model=ParseTaskItem, summary="取消解析")
def cancel_parse_task(
    task_id: uuid.UUID,
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
) -> ParseTaskItem:
    task = ParseTaskService(db).cancel(principal, task_id, ip=client_ip(request))
    return task_item(task)


@tasks_router.delete("/{task_id}", summary="删除解析任务")
def delete_parse_task(
    task_id: uuid.UUID,
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
) -> dict[str, bool]:
    ParseTaskService(db).delete(principal, task_id, ip=client_ip(request))
    return {"deleted": True}


@tasks_router.get(
    "/{task_id}/blocks", response_model=ParseBlockListResponse, summary="解析产物"
)
def list_parse_blocks(
    task_id: uuid.UUID,
    principal: ReadPrincipal,
    db: DbSession,
    include_discarded: bool = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ParseBlockListResponse:
    return ParseTaskService(db).list_blocks(
        principal,
        task_id,
        include_discarded=include_discarded,
        page=page,
        page_size=page_size,
    )
