"""解析策略与解析任务的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import BlockType, ParseTaskStatus
from app.schemas.task import TaskActions, TaskErrorInfo

DocType = Literal["pdf", "office", "image", "text", "html"]
MAX_CONFIG_KEYS = 30


class ParseStrategyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    doc_type: DocType
    engine: str = Field(min_length=1, max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
    is_default: bool = False

    @field_validator("config")
    @classmethod
    def _limit_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > MAX_CONFIG_KEYS:
            raise ValueError(f"配置项不得超过 {MAX_CONFIG_KEYS} 个")
        return value


class ParseStrategyUpdateRequest(BaseModel):
    """修改即生成新版本，因此所有字段都是可选的"覆盖值"。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    engine: str | None = Field(default=None, min_length=1, max_length=32)
    config: dict[str, Any] | None = None
    is_default: bool | None = None

    @field_validator("config")
    @classmethod
    def _limit_config(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(value) > MAX_CONFIG_KEYS:
            raise ValueError(f"配置项不得超过 {MAX_CONFIG_KEYS} 个")
        return value


class ParseStrategyItem(BaseModel):
    id: str
    strategy_key: str
    version: int
    name: str
    doc_type: str
    engine: str
    config: dict[str, Any]
    is_default: bool
    status: str
    created_at: datetime


class ParseStrategyListResponse(BaseModel):
    items: list[ParseStrategyItem]


class ParseTaskCreateRequest(BaseModel):
    document_version_id: str
    strategy_id: str | None = None


class ParseTaskQuery(BaseModel):
    document_id: str | None = None
    status: ParseTaskStatus | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class ParseAttemptItem(BaseModel):
    id: str
    attempt_no: int
    status: str
    source: str = "manual"
    quality_score: int | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    error_detail: dict[str, Any] | None = None
    duration_ms: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ParseTaskItem(BaseModel):
    id: str
    document_id: str
    document_version_id: str
    strategy_id: str
    status: str
    attempt: int
    quality_score: int | None = None
    low_quality: bool = False
    stats: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    # 对外只给"能看懂的原因"，内部错误码保留在 error_code
    error: TaskErrorInfo | None = None
    auto_retries: int = 0
    downgrade_retries: int = 0
    manual_revives: int = 0
    # 下一次执行由谁排的：pending 时前端据此区分"系统重试中"与"人工触发"
    next_source: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    block_count: int = 0


class ParseTaskDetail(ParseTaskItem):
    warnings: list[str] = Field(default_factory=list)
    attempts: list[ParseAttemptItem] = Field(default_factory=list)
    actions: TaskActions = Field(default_factory=TaskActions)


class ParseTaskListResponse(BaseModel):
    items: list[ParseTaskItem]
    total: int
    page: int
    page_size: int


class ParseBlockItem(BaseModel):
    id: str
    seq: int
    block_type: BlockType
    content: str
    page_no: int | None = None
    bbox: dict[str, Any] | None = None
    level: int | None = None
    parent_block_id: str | None = None
    is_discarded: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseBlockListResponse(BaseModel):
    items: list[ParseBlockItem]
    total: int
    page: int
    page_size: int
