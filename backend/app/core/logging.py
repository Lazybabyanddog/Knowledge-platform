"""日志与请求上下文。

request_id 通过 contextvar 传递，日志记录器自动带上，不依赖显式参数透传。
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_REQUEST_ID_HEADER = "X-Request-Id"


def get_request_id() -> str:
    return request_id_ctx.get()


def set_request_id(value: str | None = None) -> str:
    rid = value or uuid.uuid4().hex
    request_id_ctx.set(rid)
    return rid


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    if getattr(configure_logging, "_configured", False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s [%(request_id)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.addFilter(_RequestIdFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    for noisy in ("uvicorn.access", "neo4j", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    configure_logging._configured = True  # type: ignore[attr-defined]


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求生成/继承 request_id，并回写到响应头。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(_REQUEST_ID_HEADER)
        rid = set_request_id(incoming)
        try:
            response = await call_next(request)
        finally:
            pass
        response.headers[_REQUEST_ID_HEADER] = rid
        return response
