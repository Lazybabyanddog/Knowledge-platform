"""任务失败的错误码目录。

一个码对应一条对外契约：文案、建议动作、是否值得自动重试。前端不硬编码文案，
后端加码即可生效；未知码有兜底，保证页面永远有东西可显示。

``classify_exception`` 是服务层的唯一入口：把异常翻成错误码，避免每个
``except`` 分支各写一套判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.exceptions import AppError


class TaskErrorCode(StrEnum):
    """任务级错误码。字符串值即对外字段，改名等于破坏契约。"""

    LLM_TIMEOUT = "llm_timeout"
    LLM_UNAVAILABLE = "llm_unavailable"
    LLM_REJECTED = "llm_rejected"
    NEO4J_UNAVAILABLE = "neo4j_unavailable"
    PARSE_ERROR = "parse_error"
    BAD_INPUT = "bad_input"
    ONTOLOGY_MISSING = "ontology_missing"
    TASK_TIMEOUT = "task_timeout"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    message: str
    hint: str
    retryable: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


_CATALOG: dict[str, ErrorSpec] = {
    TaskErrorCode.LLM_TIMEOUT.value: ErrorSpec(
        code=TaskErrorCode.LLM_TIMEOUT.value,
        message="模型调用超时",
        hint="稍后继续执行，或降低批大小后重试",
        retryable=True,
    ),
    TaskErrorCode.LLM_UNAVAILABLE.value: ErrorSpec(
        code=TaskErrorCode.LLM_UNAVAILABLE.value,
        message="模型服务不可用",
        hint="检查模型服务状态、网络与额度后继续执行",
        retryable=True,
    ),
    TaskErrorCode.LLM_REJECTED.value: ErrorSpec(
        code=TaskErrorCode.LLM_REJECTED.value,
        message="模型拒绝请求",
        hint="检查 API Key 与模型名配置，修正后重新触发",
    ),
    TaskErrorCode.NEO4J_UNAVAILABLE.value: ErrorSpec(
        code=TaskErrorCode.NEO4J_UNAVAILABLE.value,
        message="图库写入失败",
        hint="检查图库连接后继续执行，已完成的批次不会重跑",
        retryable=True,
    ),
    TaskErrorCode.PARSE_ERROR.value: ErrorSpec(
        code=TaskErrorCode.PARSE_ERROR.value,
        message="解析失败",
        hint="检查文档格式与解析引擎配置，修正后继续执行",
    ),
    TaskErrorCode.BAD_INPUT.value: ErrorSpec(
        code=TaskErrorCode.BAD_INPUT.value,
        message="输入不可处理",
        hint="检查文档格式与解析结果，必要时重新解析",
    ),
    TaskErrorCode.ONTOLOGY_MISSING.value: ErrorSpec(
        code=TaskErrorCode.ONTOLOGY_MISSING.value,
        message="本体已变更",
        hint="触发时冻结的本体实体类已失效，请重新触发抽取",
    ),
    TaskErrorCode.TASK_TIMEOUT.value: ErrorSpec(
        code=TaskErrorCode.TASK_TIMEOUT.value,
        message="执行超时",
        hint="从断点继续执行，已完成的批次会保留",
        retryable=True,
    ),
    TaskErrorCode.ATTEMPTS_EXHAUSTED.value: ErrorSpec(
        code=TaskErrorCode.ATTEMPTS_EXHAUSTED.value,
        message="重试预算耗尽",
        hint="继续执行、跳过失败批次，或修改输入后重新触发",
    ),
    TaskErrorCode.CANCELLED.value: ErrorSpec(
        code=TaskErrorCode.CANCELLED.value,
        message="任务已取消",
        hint="需要时可以从断点继续执行",
    ),
    TaskErrorCode.INTERNAL_ERROR.value: ErrorSpec(
        code=TaskErrorCode.INTERNAL_ERROR.value,
        message="内部错误",
        hint="查看服务日志定位原因，修复后继续执行",
    ),
}

_FALLBACK = ErrorSpec(
    code="unknown_error",
    message="未知错误",
    hint="查看服务日志定位原因",
)


def error_spec(code: str | None) -> ErrorSpec:
    """未知码保留原始值而不是抹成 unknown，便于日志与前端兜底展示。"""
    if not code:
        return _FALLBACK
    spec = _CATALOG.get(code)
    if spec is not None:
        return spec
    return ErrorSpec(code=code, message=_FALLBACK.message, hint=_FALLBACK.hint)


def describe_error(code: str | None) -> dict[str, object]:
    return error_spec(code).as_dict()


def is_retryable(code: str | None) -> bool:
    return error_spec(code).retryable


def classify_exception(exc: BaseException) -> ErrorSpec:
    """把执行期异常翻成错误码：先看异常自带的码，再按类型兜底。"""
    declared = getattr(exc, "task_error_code", None)
    if isinstance(declared, str) and declared:
        return error_spec(declared)

    if isinstance(exc, AppError):
        return error_spec(TaskErrorCode.BAD_INPUT.value)

    haystack = f"{type(exc).__module__}.{type(exc).__name__} {exc}".lower()
    if "neo4j" in haystack:
        return error_spec(TaskErrorCode.NEO4J_UNAVAILABLE.value)
    # 含 Celery 软超时（SoftTimeLimitExceeded）与 requests/httpx 的超时文案
    if any(token in haystack for token in ("timeout", "timed out", "timelimit")):
        return error_spec(TaskErrorCode.TASK_TIMEOUT.value)
    if "llm" in haystack:
        return error_spec(TaskErrorCode.LLM_UNAVAILABLE.value)
    return error_spec(TaskErrorCode.INTERNAL_ERROR.value)


__all__ = [
    "ErrorSpec",
    "TaskErrorCode",
    "classify_exception",
    "describe_error",
    "error_spec",
    "is_retryable",
]
