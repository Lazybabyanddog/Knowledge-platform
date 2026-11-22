"""parse_strategy：解析策略，修改即升版本，历史任务引用旧版本。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import StrategyStatus
from app.infra.db.base import Base, UUIDPrimaryKeyMixin


class ParseStrategy(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "parse_strategy"
    __table_args__ = (
        UniqueConstraint("strategy_key", "version", name="uq_parse_strategy_key_version"),
        # 每个空间每个文档类型只允许一个默认策略
        Index(
            "uq_parse_strategy_default_per_doc_type",
            "workspace_id",
            "doc_type",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    strategy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=StrategyStatus.ACTIVE.value, server_default="active"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
