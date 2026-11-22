"""app_user：平台用户。表名避开 PostgreSQL 保留字 user。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import UserStatus
from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AppUser(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "app_user"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workspace.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=UserStatus.ACTIVE.value, server_default="active"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
