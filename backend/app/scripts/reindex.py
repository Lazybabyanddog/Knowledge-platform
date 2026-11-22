"""按已发布文档重建检索索引。

用法::

    python -m app.scripts.reindex --all
    python -m app.scripts.reindex --document-id <uuid>

重新投递的是 ``index.document_version`` 任务，不会改动文档状态；
批量重建建议单独起一个 worker，避免长时间占用解析队列。
"""

from __future__ import annotations

import argparse
import sys
import uuid

from sqlalchemy import select

from app.domain.enums import DocumentStatus
from app.infra.db.models.document import Document
from app.infra.db.session import SessionLocal
from app.services.index_service import enqueue_version_index


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="重建已发布文档的检索索引")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="重建全部已发布文档")
    group.add_argument("--document-id", help="只重建指定文档")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    with SessionLocal() as session:
        stmt = select(Document).where(
            Document.status == DocumentStatus.PUBLISHED.value,
            Document.deleted_at.is_(None),
            Document.current_version_id.is_not(None),
        )
        if args.document_id:
            try:
                document_id = uuid.UUID(args.document_id)
            except ValueError:
                print(f"文档 ID 不合法: {args.document_id}", file=sys.stderr)
                return 2
            stmt = stmt.where(Document.id == document_id)
        documents = list(session.scalars(stmt.order_by(Document.created_at)))

    for document in documents:
        if document.current_version_id:
            enqueue_version_index(document.current_version_id)

    print(f"已投递 {len(documents)} 个索引任务")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
