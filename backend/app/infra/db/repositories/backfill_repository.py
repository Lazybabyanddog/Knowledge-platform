"""extraction_backfill_batch 的查询封装。

重抽批次只增不改（除了收尾时写 ``status`` / ``report`` / ``finished_at``），
所以这里只需要"建、查、按空间列最近几批"这三种读法。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infra.db.models import ExtractionBackfillBatch


class ExtractionBackfillRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, batch: ExtractionBackfillBatch) -> ExtractionBackfillBatch:
        self.db.add(batch)
        return batch

    def get(self, batch_id: uuid.UUID) -> ExtractionBackfillBatch | None:
        return self.db.get(ExtractionBackfillBatch, batch_id)

    def list_recent(
        self, workspace_id: uuid.UUID, *, limit: int = 20
    ) -> list[ExtractionBackfillBatch]:
        return list(
            self.db.scalars(
                select(ExtractionBackfillBatch)
                .where(ExtractionBackfillBatch.workspace_id == workspace_id)
                .order_by(ExtractionBackfillBatch.created_at.desc())
                .limit(limit)
            )
        )
