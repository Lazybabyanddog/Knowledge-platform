"""知识抽取、候选审核与图谱查询接口。"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.knowledge import (
    EntityItem,
    EntityListResponse,
    EntityMergeRequest,
    EntityMergeResponse,
    EntityMergeUndoRequest,
    EntityQuery,
    EntityRejectRequest,
    EntitySourceResponse,
    EntityUpdateRequest,
    ExtractionRetryRequest,
    ExtractionTaskCreateRequest,
    ExtractionTaskDetail,
    ExtractionTaskItem,
    ExtractionTaskListResponse,
    ExtractionTaskQuery,
    RelationItem,
    RelationListResponse,
    RelationQuery,
    RelationUpdateRequest,
    SubgraphResponse,
)
from app.schemas.backfill import BackfillBatchItem, BackfillRequest
from app.services.backfill_service import BackfillService
from app.services.extraction_service import (
    ExtractionService,
    extraction_task_item,
)
from app.services.knowledge_service import KnowledgeService

DbSession = Annotated[Session, Depends(get_db)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.GRAPH_READ))]
ExtractPrincipal = Annotated[Principal, Depends(require_permission(Permission.PARSE_RUN))]
ReviewPrincipal = Annotated[
    Principal, Depends(require_permission(Permission.ONTOLOGY_REVIEW))
]

extraction_router = APIRouter(prefix="/extraction-tasks", tags=["知识抽取"])
entities_router = APIRouter(prefix="/entities", tags=["候选实体"])
relations_router = APIRouter(prefix="/relations", tags=["候选关系"])
graph_router = APIRouter(prefix="/graph", tags=["知识图谱"])


# --------------------------------------------------------------------------- #
# 抽取任务
# --------------------------------------------------------------------------- #
@extraction_router.post(
    "", response_model=ExtractionTaskItem, status_code=202, summary="触发知识抽取"
)
def trigger_extraction(
    payload: ExtractionTaskCreateRequest,
    request: Request,
    principal: ExtractPrincipal,
    db: DbSession,
) -> ExtractionTaskItem:
    task = ExtractionService(db).trigger(principal, payload, ip=client_ip(request))
    return extraction_task_item(task)


@extraction_router.get("", response_model=ExtractionTaskListResponse, summary="抽取任务列表")
def list_extraction_tasks(
    query: Annotated[ExtractionTaskQuery, Query()],
    principal: ReadPrincipal,
    db: DbSession,
) -> ExtractionTaskListResponse:
    return ExtractionService(db).list_tasks(principal, query)


@extraction_router.post(
    "/backfill", response_model=BackfillBatchItem, summary="存量重抽（运维）"
)
def run_backfill(
    payload: BackfillRequest,
    request: Request,
    principal: ExtractPrincipal,
    db: DbSession,
) -> BackfillBatchItem:
    """同步执行：一批跑完才返回，报告里带跳过/失败与无法沿用的人工结论。"""
    return BackfillService(db).run(principal, payload)


@extraction_router.get(
    "/backfill", response_model=list[BackfillBatchItem], summary="重抽批次列表"
)
def list_backfill_batches(
    principal: ExtractPrincipal,
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BackfillBatchItem]:
    return BackfillService(db).list_batches(principal, limit=limit)


@extraction_router.get(
    "/backfill/{batch_id}", response_model=BackfillBatchItem, summary="重抽批次报告"
)
def get_backfill_batch(
    batch_id: str, principal: ExtractPrincipal, db: DbSession
) -> BackfillBatchItem:
    return BackfillService(db).get(principal, batch_id)


@extraction_router.get(
    "/{task_id}", response_model=ExtractionTaskDetail, summary="抽取任务详情"
)
def get_extraction_task(
    task_id: uuid.UUID, principal: ReadPrincipal, db: DbSession
) -> ExtractionTaskDetail:
    return ExtractionService(db).get_detail(principal, task_id)


@extraction_router.post(
    "/{task_id}/retry", response_model=ExtractionTaskItem, summary="继续执行抽取"
)
def retry_extraction(
    task_id: uuid.UUID,
    request: Request,
    principal: ExtractPrincipal,
    db: DbSession,
    payload: ExtractionRetryRequest | None = None,
) -> ExtractionTaskItem:
    """从断点继续：默认沿用原批大小，``degrade`` 时同时降参。"""
    task = ExtractionService(db).retry(
        principal,
        task_id,
        degrade=bool(payload and payload.degrade),
        ip=client_ip(request),
    )
    return extraction_task_item(task)


@extraction_router.post(
    "/{task_id}/skip-failed-batches",
    response_model=ExtractionTaskItem,
    summary="跳过失败批次继续",
)
def skip_failed_batches(
    task_id: uuid.UUID,
    request: Request,
    principal: ExtractPrincipal,
    db: DbSession,
) -> ExtractionTaskItem:
    """把仍缺的批次标记为跳过并续跑，结果是"部分完成"，页面据此标注不完整。"""
    task = ExtractionService(db).skip_failed_batches(
        principal, task_id, ip=client_ip(request)
    )
    return extraction_task_item(task)


@extraction_router.post(
    "/{task_id}/cancel", response_model=ExtractionTaskItem, summary="取消抽取"
)
def cancel_extraction(
    task_id: uuid.UUID,
    request: Request,
    principal: ExtractPrincipal,
    db: DbSession,
) -> ExtractionTaskItem:
    task = ExtractionService(db).cancel(principal, task_id, ip=client_ip(request))
    return extraction_task_item(task)


# --------------------------------------------------------------------------- #
# 实体
# --------------------------------------------------------------------------- #
@entities_router.get("", response_model=EntityListResponse, summary="实体列表")
def list_entities(
    query: Annotated[EntityQuery, Query()],
    principal: ReadPrincipal,
    db: DbSession,
) -> EntityListResponse:
    return KnowledgeService(db).list_entities(principal, query)


@entities_router.post("/merge", response_model=EntityMergeResponse, summary="合并实体")
def merge_entities(
    payload: EntityMergeRequest,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> EntityMergeResponse:
    return KnowledgeService(db).merge(principal, payload, ip=client_ip(request))


@entities_router.post("/merge/undo", summary="撤销合并")
def undo_merge(
    payload: EntityMergeUndoRequest,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> dict[str, Any]:
    return KnowledgeService(db).undo_merge(principal, payload, ip=client_ip(request))


@entities_router.get("/{entity_id}", response_model=EntityItem, summary="实体详情")
def get_entity(entity_id: str, principal: ReadPrincipal, db: DbSession) -> EntityItem:
    return KnowledgeService(db).get_entity(principal, entity_id)


@entities_router.patch("/{entity_id}", response_model=EntityItem, summary="修改实体")
def update_entity(
    entity_id: str,
    payload: EntityUpdateRequest,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> EntityItem:
    return KnowledgeService(db).update_entity(
        principal, entity_id, payload, ip=client_ip(request)
    )


@entities_router.post(
    "/{entity_id}/confirm", response_model=EntityItem, summary="确认实体（可改名）"
)
def confirm_entity(
    entity_id: str,
    payload: EntityUpdateRequest,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> EntityItem:
    return KnowledgeService(db).confirm_entity(
        principal, entity_id, payload, ip=client_ip(request)
    )


@entities_router.post(
    "/{entity_id}/reject", response_model=EntityItem, summary="驳回实体（必须填理由）"
)
def reject_entity(
    entity_id: str,
    payload: EntityRejectRequest,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> EntityItem:
    return KnowledgeService(db).reject_entity(
        principal, entity_id, payload, ip=client_ip(request)
    )


@entities_router.get(
    "/{entity_id}/sources", response_model=EntitySourceResponse, summary="实体来源反查"
)
def get_entity_sources(
    entity_id: str, principal: ReadPrincipal, db: DbSession
) -> EntitySourceResponse:
    return KnowledgeService(db).entity_sources(principal, entity_id)


# --------------------------------------------------------------------------- #
# 关系
# --------------------------------------------------------------------------- #
@relations_router.get("", response_model=RelationListResponse, summary="关系列表")
def list_relations(
    query: Annotated[RelationQuery, Query()],
    principal: ReadPrincipal,
    db: DbSession,
) -> RelationListResponse:
    return KnowledgeService(db).list_relations(principal, query)


@relations_router.get("/{relation_id}", response_model=RelationItem, summary="关系详情")
def get_relation(
    relation_id: str, principal: ReadPrincipal, db: DbSession
) -> RelationItem:
    return KnowledgeService(db).get_relation(principal, relation_id)


@relations_router.patch(
    "/{relation_id}", response_model=RelationItem, summary="修改关系属性/描述"
)
def update_relation(
    relation_id: str,
    payload: RelationUpdateRequest,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> RelationItem:
    return KnowledgeService(db).update_relation(
        principal, relation_id, payload, ip=client_ip(request)
    )


@relations_router.post(
    "/{relation_id}/confirm", response_model=RelationItem, summary="确认关系"
)
def confirm_relation(
    relation_id: str,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> RelationItem:
    return KnowledgeService(db).confirm_relation(
        principal, relation_id, ip=client_ip(request)
    )


@relations_router.post(
    "/{relation_id}/reject", response_model=RelationItem, summary="驳回关系（必须填理由）"
)
def reject_relation(
    relation_id: str,
    payload: EntityRejectRequest,
    request: Request,
    principal: ReviewPrincipal,
    db: DbSession,
) -> RelationItem:
    return KnowledgeService(db).reject_relation(
        principal, relation_id, payload, ip=client_ip(request)
    )


# --------------------------------------------------------------------------- #
# 子图与文档关联
# --------------------------------------------------------------------------- #
@graph_router.get(
    "/entities/{entity_id}/subgraph", response_model=SubgraphResponse, summary="实体子图"
)
def get_subgraph(
    entity_id: str,
    principal: ReadPrincipal,
    db: DbSession,
    depth: Annotated[int, Query(ge=1, le=3)] = 1,
    include_proposed: bool = False,
) -> SubgraphResponse:
    return KnowledgeService(db).subgraph(
        principal, entity_id, depth=depth, include_proposed=include_proposed
    )


@graph_router.get(
    "/documents/{document_id}/entities",
    response_model=EntityListResponse,
    summary="文档关联实体",
)
def list_document_entities(
    document_id: str,
    principal: ReadPrincipal,
    db: DbSession,
    status: Literal["proposed", "confirmed", "rejected", "merged"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> EntityListResponse:
    return KnowledgeService(db).list_document_entities(
        principal, document_id, status=status, page=page, page_size=page_size
    )
