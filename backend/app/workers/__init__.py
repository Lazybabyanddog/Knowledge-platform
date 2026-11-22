"""Celery 后台任务：解析、抽取等长耗时作业都从这里进入。"""

from app.workers.celery_app import celery_app

__all__ = ["celery_app"]
