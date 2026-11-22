"""知识检索与问答接口。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.deps import Principal, client_ip, require_permission
from app.core.exceptions import ServiceUnavailableError
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.infra.llm import provider
from app.schemas.retrieval import (
    AskRequest,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
    RetrievalSource,
)
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
        hits = await run_in_threadpool(
            service.search,
            principal,
            payload.question,
            top_k=payload.top_k,
            doc_type=payload.doc_type,
        )
    except RetrievalUnavailable as exc:
        raise ServiceUnavailableError(str(exc)) from exc

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
    return StreamingResponse(
        _event_stream(payload.question, hits, sources, request, started),
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
) -> AsyncIterator[str]:
    yield _sse("sources", {"sources": sources})

    if not hits:
        yield _sse("delta", {"text": NO_HIT_ANSWER})
        yield _sse("done", {"elapsed_ms": _elapsed(started), "sources": 0})
        return

    stream = provider.astream_chat(build_ask_messages(question, hits))
    aborted = False
    try:
        async for delta in stream:
            if await request.is_disconnected():
                logger.info("问答客户端已断开，终止上游生成")
                aborted = True
                break
            yield _sse("delta", {"text": delta})
    except provider.LLMError as exc:
        yield _sse("error", {"code": "LLM_CALL_FAILED", "message": str(exc)})
        return
    except Exception as exc:  # noqa: BLE001 - 流已开始，只能以事件形式报错
        logger.exception("问答生成失败")
        yield _sse("error", {"code": "LLM_CALL_FAILED", "message": str(exc)})
        return
    finally:
        # 显式关闭上游连接，避免客户端断开后仍占着连接
        await stream.aclose()

    if aborted:
        return

    yield _sse("done", {"elapsed_ms": _elapsed(started), "sources": len(hits)})


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = ["router"]
