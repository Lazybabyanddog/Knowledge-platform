"""audit_log：统一审计日志，按 created_at 月分区。

分区表的主键必须包含分区键，因此主键为 (id, created_at)；
id 使用共享的 identity 序列，跨分区仍然全局唯一、可按插入顺序排序。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now(), index=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_name: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_type: Mapped[str | None] = mapped_column(String(32))
    object_id: Mapped[str | None] = mapped_column(String(64), index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
    request_id: Mapped[str | None] = mapped_column(String(64))
