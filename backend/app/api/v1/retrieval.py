"""知识检索与问答接口。"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.deps import Principal, client_ip, require_permission
from app.core.exceptions import ServiceUnavailableError
from app.domain.enums import QaAnswerStatus
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.infra.llm import provider
from app.schemas.query_rewrite import RewriteRunRequest, RewriteTrace
from app.schemas.retrieval import (
    AskRequest,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
    RetrievalSource,
)
from app.services.qa_service import QaService
from app.services.query_rewrite.service import QueryRewriteService
from app.services.retrieval_service import (
    NO_HIT_ANSWER,
    ChunkHit,
    RetrievalService,
    RetrievalUnavailable,
    build_ask_messages,
    hit_to_source,
)

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_db)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.DOCUMENT_READ))]

router = APIRouter(prefix="/retrieval", tags=["知识检索"])


@router.post(
    "/search",
    response_model=RetrievalSearchResponse,
    summary="片段检索（向量召回）",
)
def search(
    payload: RetrievalSearchRequest,
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
) -> RetrievalSearchResponse:
    try:
        hits = RetrievalService(db).search(
            principal,
            payload.query,
            top_k=payload.top_k,
            doc_type=payload.doc_type,
        )
    except RetrievalUnavailable as exc:
        # 不做"空结果"伪装：未配置向量模型是运维问题，调用方需要能区分
        raise ServiceUnavailableError(str(exc)) from exc

    items = [
        RetrievalSource(**hit_to_source(hit, index)) for index, hit in enumerate(hits, start=1)
    ]
    return RetrievalSearchResponse(items=items, total=len(items))


@router.post(
    "/rewrite-lab/run",
    response_model=RewriteTrace,
    summary="改写实验室：运行一次查询改写",
)
def rewrite_lab_run(
    payload: RewriteRunRequest,
    principal: ReadPrincipal,
    db: DbSession,
) -> RewriteTrace:
    """只读诊断：返回完整 Trace，不写审计、不改动文档与索引。"""
    if not provider.is_embedding_configured():
        # 没有向量模型连对照组都跑不出来，这里和 /search 保持一致直接 503
        raise ServiceUnavailableError("未配置 embedding 模型，无法检索")
    try:
        return QueryRewriteService(db).run(principal, payload)
    except RetrievalUnavailable as exc:
        raise ServiceUnavailableError(str(exc)) from exc


@router.post("/ask", summary="知识问答（SSE 流式）")
async def ask(
    payload: AskRequest,
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
) -> StreamingResponse:
    # 在开始流式响应之前判断，保证调用方拿到的是 503 而不是半截事件流
    if not provider.is_embedding_configured():
        raise ServiceUnavailableError("未配置 embedding 模型，无法检索")
    if not provider.is_llm_configured():
        raise ServiceUnavailableError("未配置 LLM，无法生成回答")

    started = time.perf_counter()
    service = RetrievalService(db)
    try:
        hits, question_vec = await run_in_threadpool(
            service.search_with_vector,
            principal,
            payload.question,
            top_k=payload.top_k,
            doc_type=payload.doc_type,
        )
    except RetrievalUnavailable as exc:
        raise ServiceUnavailableError(str(exc)) from exc

    # 先落一条 streaming 记录：``done`` 要带标识，而标识必须先于答案存在。
    # 与此同时答案正文由流结束后另行补写，不在这里等。
    record = await run_in_threadpool(
        QaService(db).open_record, principal, payload.question, hits, question_vec
    )
    qa_record_id = record.id

    # 审计与检索都在进入流之前完成：流开始后 session 生命周期由框架接管
    await run_in_threadpool(
        service.record_ask,
        principal,
        payload.question,
        len(hits),
        ip=client_ip(request),
    )
    await run_in_threadpool(db.commit)

    sources = [hit_to_source(hit, index) for index, hit in enumerate(hits, start=1)]
    glossary_lines = sum(len(hit.glossary) for hit in hits)
    return StreamingResponse(
        _event_stream(
            payload.question, hits, sources, request, started, qa_record_id, glossary_lines
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    """单行 JSON：事件以空行分隔，data 内不能有裸换行。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(
    question: str,
    hits: list[ChunkHit],
    sources: list[dict[str, Any]],
    request: Request,
    started: float,
    qa_record_id: uuid.UUID,
    glossary_lines: int = 0,
) -> AsyncIterator[str]:
    yield _sse("sources", {"sources": sources})

    if not hits:
        yield _sse("delta", {"text": NO_HIT_ANSWER})
        await _finalize(qa_record_id, NO_HIT_ANSWER, QaAnswerStatus.COMPLETED, started)
        yield _sse(
            "done",
            {
                "elapsed_ms": _elapsed(started),
                "sources": 0,
                "qa_record_id": str(qa_record_id),
                "glossary_lines": 0,
            },
        )
        return

    stream = provider.astream_chat(build_ask_messages(question, hits))
    chunks: list[str] = []
    aborted = False
    try:
        async for delta in stream:
            if await request.is_disconnected():
                logger.info("问答客户端已断开，终止上游生成")
                aborted = True
                break
            chunks.append(delta)
            yield _sse("delta", {"text": delta})
    except provider.LLMError as exc:
        await _finalize(qa_record_id, "".join(chunks), QaAnswerStatus.FAILED, started)
        yield _sse("error", {"code": "LLM_CALL_FAILED", "message": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001 - 流已开始，只能以事件形式报错
        logger.exception("问答生成失败")
        await _finalize(qa_record_id, "".join(chunks), QaAnswerStatus.FAILED, started)
        yield _sse("error", {"code": "LLM_CALL_FAILED", "message": str(exc)})
        return
    finally:
        # 显式关闭上游连接，避免客户端断开后仍占着连接
        await stream.aclose()

    if aborted:
        # 中断的答案不完整，没有可提交反馈的标识，也不进治理队列
        await _finalize(qa_record_id, "".join(chunks), QaAnswerStatus.INTERRUPTED, started)
        return

    answer = "".join(chunks)
    # 先补全记录再发 done：前端拿到标识就能立刻提交评价，不会读到还没落库的记录
    await _finalize(qa_record_id, answer, QaAnswerStatus.COMPLETED, started)
    yield _sse(
        "done",
        {
            "elapsed_ms": _elapsed(started),
            "sources": len(hits),
            "qa_record_id": str(qa_record_id),
            "glossary_lines": glossary_lines,
        },
    )


async def _finalize(
    record_id: uuid.UUID, answer: str, status: QaAnswerStatus, started: float
) -> None:
    """独立会话补写终态：请求会话在流期间已关闭，不能复用。"""
    await run_in_threadpool(QaService.finalize, record_id, answer, status, _elapsed(started))


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = ["router"]
