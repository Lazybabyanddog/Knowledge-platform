"""Celery 应用：broker/backend 复用 Redis，解析任务单独走 parse 队列。"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "ai_platform",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 长任务：先取出再确认，worker 崩溃时消息可重新投递
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    result_expires=3600,
    task_routes={
        # 解析与抽取都是长任务，共用一个队列，部署时一个 worker 即可消费
        "parse.document": {"queue": "parse"},
        "extract.knowledge": {"queue": "parse"},
        # 索引任务同样走 parse 队列：worker 已常驻，无需额外进程；
        # 批量重建时建议另起一个 `-Q parse` worker，避免长占用阻塞解析
        "index.document_version": {"queue": "parse"},
        "index.purge": {"queue": "parse"},
    },
)

celery_app.autodiscover_tasks(["app.workers"])
