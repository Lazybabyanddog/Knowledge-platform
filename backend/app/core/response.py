"""统一响应包装。

两层：
1. ``envelope()`` 构造标准响应体；
2. ``EnvelopeMiddleware`` 把 ``/api`` 下 router 返回的裸数据自动包起来，
   业务代码只写 ``return {"id": ...}``，不必重复套壳。
"""

from __future__ import annotations

import json
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging import get_request_id


def envelope(
    data: Any = None,
    *,
    code: int = 0,
    message: str = "ok",
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "request_id": request_id or get_request_id(),
    }


def ok(data: Any = None, *, message: str = "ok", status_code: int = 200) -> JSONResponse:
    return JSONResponse(envelope(data, message=message), status_code=status_code)


def _is_enveloped(payload: Any) -> bool:
    return (
        isinstance(payload, dict)
        and "code" in payload
        and "message" in payload
        and "request_id" in payload
    )


class EnvelopeMiddleware(BaseHTTPMiddleware):
    """把 /api 下的 JSON 成功响应统一包装为 {code, message, data, request_id}。"""

    def __init__(self, app, prefix: str | None = None) -> None:  # noqa: ANN001
        super().__init__(app)
        self.prefix = prefix or settings.api_prefix

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        if not request.url.path.startswith(self.prefix):
            return response
        if response.status_code >= 400:
            return response

        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/json":
            return response

        body = b""
        async for chunk in response.body_iterator:  # type: ignore[attr-defined]
            body += chunk if isinstance(chunk, bytes) else chunk.encode()

        try:
            payload = json.loads(body) if body else None
        except json.JSONDecodeError:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json",
            )

        if _is_enveloped(payload):
            wrapped = payload
        else:
            wrapped = envelope(payload)

        return JSONResponse(wrapped, status_code=response.status_code)
