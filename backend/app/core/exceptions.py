"""业务异常与错误码。

约定：所有对外错误统一为 ``{"code": <业务码>, "message": ..., "data": ...}``，
HTTP 状态码由业务码映射得出，避免 router 里手写 status_code。
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorCode(IntEnum):
    OK = 0
    BAD_REQUEST = 40001
    UNAUTHORIZED = 40101
    TOKEN_EXPIRED = 40102
    FORBIDDEN = 40301
    NOT_FOUND = 40401
    CONFLICT = 40901
    PAYLOAD_TOO_LARGE = 41301
    TOO_MANY_REQUESTS = 42901
    INTERNAL = 50000
    UNAVAILABLE = 50301


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.OK: 200,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.TOKEN_EXPIRED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.TOO_MANY_REQUESTS: 429,
    ErrorCode.INTERNAL: 500,
    ErrorCode.UNAVAILABLE: 503,
}

DEFAULT_MESSAGE: dict[ErrorCode, str] = {
    ErrorCode.OK: "ok",
    ErrorCode.BAD_REQUEST: "参数校验失败",
    ErrorCode.UNAUTHORIZED: "未认证",
    ErrorCode.TOKEN_EXPIRED: "令牌已过期",
    ErrorCode.FORBIDDEN: "权限不足",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.CONFLICT: "状态冲突或资源重复",
    ErrorCode.PAYLOAD_TOO_LARGE: "文件超出大小限制",
    ErrorCode.TOO_MANY_REQUESTS: "请求过于频繁",
    ErrorCode.INTERNAL: "服务内部错误",
    ErrorCode.UNAVAILABLE: "服务暂不可用",
}


class AppError(Exception):
    """所有可预期业务异常的基类。"""

    code: ErrorCode = ErrorCode.INTERNAL

    def __init__(
        self,
        message: str | None = None,
        *,
        code: ErrorCode | None = None,
        data: Any = None,
    ) -> None:
        if code is not None:
            self.code = code
        self.message = message or DEFAULT_MESSAGE.get(self.code, "服务内部错误")
        self.data = data
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)


class BadRequestError(AppError):
    code = ErrorCode.BAD_REQUEST


class UnauthorizedError(AppError):
    code = ErrorCode.UNAUTHORIZED


class TokenExpiredError(AppError):
    code = ErrorCode.TOKEN_EXPIRED


class ForbiddenError(AppError):
    code = ErrorCode.FORBIDDEN


class NotFoundError(AppError):
    code = ErrorCode.NOT_FOUND


class ConflictError(AppError):
    code = ErrorCode.CONFLICT


class PayloadTooLargeError(AppError):
    code = ErrorCode.PAYLOAD_TOO_LARGE


class TooManyRequestsError(AppError):
    code = ErrorCode.TOO_MANY_REQUESTS


class InternalError(AppError):
    code = ErrorCode.INTERNAL


class ServiceUnavailableError(AppError):
    """依赖能力未就绪（如未配置 embedding / LLM），不是参数错误也不该伪装成空结果。"""

    code = ErrorCode.UNAVAILABLE


class TaskFatalError(AppError):
    """任务级失败的基类：``task_error_code`` 会被服务层落进执行记录。

    和普通业务异常的区别只在于"附带一个可展示的错误码"，HTTP 语义仍由 ``code`` 决定。
    """

    task_error_code: str = "internal_error"


class BadInputError(TaskFatalError):
    """输入不可处理（文件损坏、内容块为空）：重试与降参都无意义。"""

    code = ErrorCode.BAD_REQUEST
    task_error_code = "bad_input"


class OntologyMissingError(TaskFatalError):
    """触发时冻结的本体实体类已失效：重试无用，只能重新触发。"""

    code = ErrorCode.BAD_REQUEST
    task_error_code = "ontology_missing"


# --------------------------------------------------------------------------- #
# 全局异常处理器
# --------------------------------------------------------------------------- #

_HTTP_STATUS_TO_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.BAD_REQUEST,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    422: ErrorCode.BAD_REQUEST,
    429: ErrorCode.TOO_MANY_REQUESTS,
}


def register_exception_handlers(app: Any) -> None:
    """把 AppError / 校验错误 / 未捕获异常统一转成标准响应体。"""
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import JSONResponse

    from app.core.logging import get_request_id
    from app.core.response import envelope

    def _error_response(code: ErrorCode, message: str, data: Any = None) -> JSONResponse:
        body = envelope(data, code=int(code), message=message, request_id=get_request_id())
        return JSONResponse(body, status_code=HTTP_STATUS.get(code, 500))

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Any, exc: AppError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.data)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_: Any, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(part) for part in err.get("loc", [])),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        # 模型校验器抛出的业务提示（如"标识键 xxx 重复"）比"参数校验失败"有用得多，
        # 直接放进 message 让调用方与界面能定位问题；类型错误仍用统一文案。
        message = DEFAULT_MESSAGE[ErrorCode.BAD_REQUEST]
        for err in exc.errors():
            if err.get("type") == "value_error":
                text = str(err.get("msg", "")).removeprefix("Value error, ").strip()
                if text:
                    message = text
                break
        return _error_response(ErrorCode.BAD_REQUEST, message, details)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(_: Any, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL)
        message = exc.detail if isinstance(exc.detail, str) else DEFAULT_MESSAGE[code]
        return _error_response(code, message)

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Any, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger(__name__).exception("未处理异常: %s", exc)
        return _error_response(ErrorCode.INTERNAL, DEFAULT_MESSAGE[ErrorCode.INTERNAL])
