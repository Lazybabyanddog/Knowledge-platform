"""refresh_token 的查询与吊销封装。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.infra.db.models import RefreshToken


class RefreshTokenRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None,
        ip: str | None,
    ) -> RefreshToken:
        record = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return self.db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    def revoke(self, record: RefreshToken) -> None:
        if record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)

    def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        result = self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        return result.rowcount or 0
