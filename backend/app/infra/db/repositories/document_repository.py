"""文档与版本的查询入口，SQL 只写在这里。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.infra.db.models import Document, DocumentVersion


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- 文档 ----
    def add(self, document: Document) -> Document:
        self.db.add(document)
        return document

    def get(
        self,
        document_id: uuid.UUID,
        *,
        workspace_id: uuid.UUID | None = None,
        include_deleted: bool = False,
    ) -> Document | None:
        conditions = [Document.id == document_id]
        if workspace_id is not None:
            conditions.append(Document.workspace_id == workspace_id)
        if not include_deleted:
            conditions.append(Document.deleted_at.is_(None))
        return self.db.scalar(select(Document).where(*conditions))

    def list_paginated(
        self,
        *,
        workspace_id: uuid.UUID,
        keyword: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        sensitivity: str | None = None,
        owner_id: uuid.UUID | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        include_deleted: bool = False,
        sort_by: str = "created_at",
        order: str = "desc",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Document], int]:
        conditions = [Document.workspace_id == workspace_id]
        if not include_deleted:
            conditions.append(Document.deleted_at.is_(None))
        if keyword:
            conditions.append(Document.title.ilike(f"%{keyword}%"))
        if status:
            conditions.append(Document.status == status)
        if tag:
            conditions.append(Document.tags.contains([tag]))
        if sensitivity:
            conditions.append(Document.sensitivity == sensitivity)
        if owner_id is not None:
            conditions.append(Document.owner_id == owner_id)
        if created_from is not None:
            conditions.append(Document.created_at >= created_from)
        if created_to is not None:
            conditions.append(Document.created_at <= created_to)

        total = self.db.scalar(
            select(func.count()).select_from(Document).where(*conditions)
        )

        column = Document.updated_at if sort_by == "updated_at" else Document.created_at
        ordering = column.asc() if order == "asc" else column.desc()
        rows = self.db.scalars(
            select(Document)
            .where(*conditions)
            .order_by(ordering, Document.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return list(rows), int(total or 0)

    # ---- 版本 ----
    def add_version(self, version: DocumentVersion) -> DocumentVersion:
        self.db.add(version)
        return version

    def list_versions(self, document_id: uuid.UUID) -> list[DocumentVersion]:
        return list(
            self.db.scalars(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(DocumentVersion.version_no.desc())
            ).all()
        )

    def get_version(
        self, document_id: uuid.UUID, version_id: uuid.UUID
    ) -> DocumentVersion | None:
        return self.db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == version_id,
                DocumentVersion.document_id == document_id,
            )
        )

    def get_version_by_no(
        self, document_id: uuid.UUID, version_no: int
    ) -> DocumentVersion | None:
        return self.db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_no == version_no,
            )
        )

    def get_versions(self, version_ids: list[uuid.UUID]) -> dict[uuid.UUID, DocumentVersion]:
        if not version_ids:
            return {}
        rows = self.db.scalars(
            select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))
        ).all()
        return {row.id: row for row in rows}

    def max_version_no(self, document_id: uuid.UUID) -> int:
        value = self.db.scalar(
            select(func.max(DocumentVersion.version_no)).where(
                DocumentVersion.document_id == document_id
            )
        )
        return int(value or 0)

    def checksum_exists(self, document_id: uuid.UUID, checksum: str) -> bool:
        stmt: Select = select(DocumentVersion.id).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.checksum == checksum,
        )
        return self.db.scalar(stmt) is not None
