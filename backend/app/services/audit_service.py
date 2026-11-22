"""审计服务：统一写入 audit_log，按月份自动补建分区。

两种写入方式：
- ``record()``：与业务操作共享同一会话，随业务事务一起提交（成功即留痕）；
- ``record_isolated()``：独立会话立即提交，用于"业务要抛异常但仍需留痕"的场景，
  例如登录失败——主会话会回滚，审计必须单独落地。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_request_id
from app.domain.enums import AuditAction
from app.infra.db.models import AppUser, AuditLog
from app.infra.db.session import session_scope

logger = logging.getLogger(__name__)

PARTITION_PREFIX = "audit_log_"
_ENSURED_PARTITIONS: set[str] = set()


def _month_bounds(when: datetime) -> tuple[datetime, datetime]:
    start = datetime(when.year, when.month, 1, tzinfo=UTC)
    end = (
        datetime(when.year + 1, 1, 1, tzinfo=UTC)
        if when.month == 12
        else datetime(when.year, when.month + 1, 1, tzinfo=UTC)
    )
    return start, end


def partition_name(when: datetime) -> str:
    return f"{PARTITION_PREFIX}{when.year:04d}{when.month:02d}"


def ensure_partition(db: Session, when: datetime | None = None) -> str:
    """确保目标月份分区存在；每月只做一次 DDL，其后走进程内缓存。"""
    when = when or datetime.now(UTC)
    name = partition_name(when)
    if name in _ENSURED_PARTITIONS:
        return name

    exists = db.scalar(text("SELECT to_regclass(:name)"), {"name": name})
    if not exists:
        start, end = _month_bounds(when)
        db.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF audit_log "
                f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
            )
        )
    _ENSURED_PARTITIONS.add(name)
    return name


class AuditService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record(
        self,
        action: AuditAction | str,
        *,
        actor: AppUser | None = None,
        actor_id: uuid.UUID | None = None,
        actor_name: str | None = None,
        workspace_id: uuid.UUID | None = None,
        object_type: str | None = None,
        object_id: str | uuid.UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        request_id: str | None = None,
    ) -> AuditLog:
        """写入一条审计；调用方负责提交（与业务同一事务）。"""
        ensure_partition(self.db)
        if actor is not None:
            actor_id = actor.id
            actor_name = actor.username
            workspace_id = workspace_id or actor.workspace_id

        row = AuditLog(
            workspace_id=workspace_id,
            actor_id=actor_id,
            actor_name=actor_name,
            action=str(action),
            object_type=object_type,
            object_id=str(object_id) if object_id is not None else None,
            before=before,
            after=after,
            ip=ip,
            request_id=request_id or get_request_id(),
        )
        self.db.add(row)
        return row

    @classmethod
    def record_isolated(cls, action: AuditAction | str, **kwargs: Any) -> None:
        """独立事务写入，失败只记日志，绝不掩盖业务异常的传播。"""
        try:
            with session_scope() as db:
                cls(db).record(action, **kwargs)
        except Exception:  # pragma: no cover - 审计失败不应影响主流程
            logger.warning("审计写入失败 action=%s", action, exc_info=True)
