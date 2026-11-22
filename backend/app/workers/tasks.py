"""解析与抽取任务入口：worker 内独立开会话，执行完立即释放。"""

from __future__ import annotations

import uuid

from app.infra.db.session import SessionLocal
from app.workers.celery_app import celery_app


@celery_app.task(name="parse.document", bind=True, max_retries=0)
def run_parse_task(self, task_id: str) -> None:  # noqa: ANN001 - Celery 注入 self
    from app.services.parse_task_service import ParseTaskService

    db = SessionLocal()
    try:
        ParseTaskService(db).execute(uuid.UUID(task_id))
    finally:
        db.close()


@celery_app.task(name="extract.knowledge", bind=True, max_retries=0)
def run_extraction_task(self, task_id: str) -> None:  # noqa: ANN001 - Celery 注入 self
    from app.services.extraction_service import ExtractionService

    db = SessionLocal()
    try:
        ExtractionService(db).execute(uuid.UUID(task_id))
    finally:
        db.close()


@celery_app.task(name="index.document_version", bind=True, max_retries=0)
def run_index_version(self, version_id: str) -> None:  # noqa: ANN001 - Celery 注入 self
    """为一个文档版本重建索引；失败时把原因写回版本，便于页面展示与重试。"""
    from app.services.index_service import IndexBuildError, build_version_index, mark_index_failed

    db = SessionLocal()
    try:
        build_version_index(db, uuid.UUID(version_id))
        db.commit()
    except IndexBuildError as exc:
        db.rollback()
        mark_index_failed(db, uuid.UUID(version_id), str(exc))
        raise
    except Exception as exc:  # noqa: BLE001 - 任何异常都要留下可诊断状态
        db.rollback()
        mark_index_failed(db, uuid.UUID(version_id), f"索引任务异常: {exc}")
        raise
    finally:
        db.close()


@celery_app.task(name="index.purge", bind=True, max_retries=0)
def run_index_purge(self, version_id: str) -> None:  # noqa: ANN001 - Celery 注入 self
    """清理版本索引：下架、删除、重新解析时调用。"""
    from app.infra.db.models.document import DocumentVersion
    from app.services.index_service import purge_version_chunks

    db = SessionLocal()
    try:
        version = db.get(DocumentVersion, uuid.UUID(version_id))
        if version is not None:
            purge_version_chunks(db, version)
        db.commit()
    finally:
        db.close()
