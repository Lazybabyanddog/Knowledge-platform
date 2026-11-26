"""任务队列名与"按引擎分流"的规则集中在这里。

MinerU 一次解析是分钟级、且要独占 GPU；和索引、看护共用一个 solo worker
会把整条链路堵住。因此它单独占一个队列，由独立 worker 消费。
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.infra.db.models import Document, ParseStrategy, ParseTask
from app.parsing.routing import resolve_effective_engine_name

PARSE_QUEUE = "parse"
MINERU_QUEUE = "parse.mineru"


def queue_for_engine(engine_name: str) -> str:
    return MINERU_QUEUE if engine_name == "mineru" else PARSE_QUEUE


def resolve_parse_queue(db: Session, task_id: str) -> str:
    """按"文档类型 + 策略引擎"选队列；任何一环查不到都回默认队列。

    与图节点共用 ``resolve_effective_engine``：策略指定的引擎不被该文档类型支持时，
    任务实际会用兜底引擎跑，队列也必须跟着回到 ``parse``，否则会投到一个没人消费的队列。
    """
    try:
        task_uuid = uuid.UUID(str(task_id))
    except (TypeError, ValueError):
        return PARSE_QUEUE

    task = db.get(ParseTask, task_uuid)
    if task is None:
        return PARSE_QUEUE
    document = db.get(Document, task.document_id)
    if document is None:
        return PARSE_QUEUE
    strategy = db.get(ParseStrategy, task.strategy_id)
    engine_name = strategy.engine if strategy is not None else None
    return queue_for_engine(resolve_effective_engine_name(document.doc_type, engine_name))


__all__ = [
    "MINERU_QUEUE",
    "PARSE_QUEUE",
    "queue_for_engine",
    "resolve_parse_queue",
]
