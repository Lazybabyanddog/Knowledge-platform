"""把解析产物变成可检索片段并写入向量。

一次构建以「文档版本」为单位：先清空该版本的片段，再按块重新切分、批量向量化，
最后把结果与状态写回 ``document_version``。重复执行结果一致。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.enums import IndexStatus, ParseTaskStatus
from app.infra.db.models.document import Document, DocumentVersion
from app.infra.db.models.parse_strategy import ParseStrategy
from app.infra.db.models.parse_task import ParseBlock, ParseTask
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.llm import provider
from app.services.chunk_config import ChunkConfig, ChunkConfigError, parse_chunk_config
from app.services.chunking import ChunkDraft, build_chunks
from app.services.glossary.config import AliasConfig, AliasConfigError, parse_alias_config
from app.services.glossary.pipeline import build_glossary
from app.services.glossary.snapshot import degraded

logger = logging.getLogger(__name__)


class IndexBuildError(RuntimeError):
    """索引构建失败，消息会写入 ``document_version.index_error``。"""


def purge_version_chunks(session: Session, version: DocumentVersion) -> int:
    """删除某版本的全部片段（含向量），返回删除行数。"""
    result = session.execute(
        delete(DocumentChunk).where(DocumentChunk.document_version_id == version.id)
    )
    version.chunk_count = 0
    return result.rowcount or 0


def purge_document_chunks(session: Session, document_id: uuid.UUID) -> int:
    result = session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    return result.rowcount or 0


def mark_index_failed(session: Session, version_id: uuid.UUID, message: str) -> None:
    """把失败原因落库。

    构建失败时上层会先 rollback 丢掉半成品，因此这里独立提交一次，
    否则状态与错误信息会随回滚一起消失。
    """
    version = session.get(DocumentVersion, version_id)
    if version is None:
        return
    version.index_status = IndexStatus.FAILED.value
    version.index_error = message[:2000]
    version.indexed_at = datetime.now(UTC)
    version.chunk_count = 0
    session.commit()


def reset_version_index(session: Session, version: DocumentVersion) -> int:
    """把版本恢复成「未索引」：清片段、清状态。"""
    removed = purge_version_chunks(session, version)
    version.index_status = IndexStatus.PENDING.value
    version.index_error = None
    version.indexed_at = None
    session.flush()
    return removed


# ---------------------------------------------------------------------- #
# 任务投递
# ---------------------------------------------------------------------- #
Dispatcher = Callable[[str], None]


def _default_index_dispatch(version_id: str) -> None:
    from app.workers.tasks import run_index_version  # 延迟导入，避免 service <-> worker 循环

    run_index_version.delay(version_id)


def _default_purge_dispatch(version_id: str) -> None:
    from app.workers.tasks import run_index_purge

    run_index_purge.delay(version_id)


_index_dispatcher: Dispatcher = _default_index_dispatch
_purge_dispatcher: Dispatcher = _default_purge_dispatch


def set_dispatcher(dispatcher: Dispatcher | None) -> None:
    """测试与运维可替换投递方式（同步执行 / 其他队列）。"""
    global _index_dispatcher
    _index_dispatcher = dispatcher or _default_index_dispatch


def set_purge_dispatcher(dispatcher: Dispatcher | None) -> None:
    global _purge_dispatcher
    _purge_dispatcher = dispatcher or _default_purge_dispatch


def enqueue_version_index(version_id: uuid.UUID) -> None:
    """投递建索引任务；broker 不可用时只记日志，不影响已完成的业务动作。"""
    try:
        _index_dispatcher(str(version_id))
    except Exception:  # noqa: BLE001 - 投递失败不应让发布回滚
        logger.warning("索引任务投递失败 version=%s", version_id, exc_info=True)


def enqueue_version_purge(version_id: uuid.UUID) -> None:
    try:
        _purge_dispatcher(str(version_id))
    except Exception:  # noqa: BLE001
        logger.warning("索引清理任务投递失败 version=%s", version_id, exc_info=True)


def _resolve_parse_task(session: Session, version: DocumentVersion) -> ParseTask | None:
    """取该版本最近一次成功的解析任务；还没解析完就没有可索引内容。"""
    return session.scalars(
        select(ParseTask)
        .where(
            ParseTask.document_version_id == version.id,
            ParseTask.status == ParseTaskStatus.SUCCEEDED.value,
        )
        .order_by(ParseTask.created_at.desc())
        .limit(1)
    ).first()


def _chunk_config_for(session: Session, task: ParseTask) -> ChunkConfig:
    """按该次解析任务关联的策略版本决定切块方式。

    片段通过 ``parse_task_id -> strategy_id`` 可追溯到配置，片段本身不存策略字段。
    策略缺失或历史数据非法时回退到默认 ``block_merge``，保证索引仍能构建。
    """
    strategy = session.get(ParseStrategy, task.strategy_id)
    try:
        return parse_chunk_config(strategy.config if strategy is not None else None)
    except ChunkConfigError:
        logger.warning("解析策略切块配置非法，回退默认策略 strategy=%s", task.strategy_id)
        return ChunkConfig()


def _alias_config_for(session: Session, task: ParseTask) -> tuple[AliasConfig, str | None]:
    """按解析任务关联的策略版本取别名配置；非法配置不抛错，返回原因供降级记录。"""
    strategy = session.get(ParseStrategy, task.strategy_id)
    try:
        return parse_alias_config(strategy.config if strategy is not None else None), None
    except AliasConfigError as exc:
        logger.warning("解析策略别名配置非法，跳过词典构建 strategy=%s", task.strategy_id)
        return AliasConfig(enabled=False), str(exc)


def _apply_glossary(session: Session, task: ParseTask, blocks: list[ParseBlock]) -> None:
    """产出本文档的别名词典并写进解析任务统计。

    词典只服务生成阶段，是纯派生物：任何失败都降级为"没有词典"，绝不让索引失败。
    """
    stats = dict(task.stats or {})
    config, config_error = _alias_config_for(session, task)
    if config_error is not None:
        stats["glossary"] = degraded(f"别名配置非法: {config_error}")
    elif not config.enabled:
        # 关闭即回到上线前：连键都不留，避免旧词典继续被注入
        stats.pop("glossary", None)
    else:
        try:
            stats["glossary"] = build_glossary(blocks, config)
        except Exception as exc:  # noqa: BLE001 - 派生物失败不阻塞索引
            logger.warning("别名抽取失败 task=%s", task.id, exc_info=True)
            stats["glossary"] = degraded(f"{type(exc).__name__}: {exc}")
    # JSONB 必须整体重新赋值，原地修改不会被识别为变更
    task.stats = stats
    session.flush()


def build_version_index(session: Session, version_id: uuid.UUID) -> DocumentVersion:
    """构建一个版本的索引。

    调用方负责 commit；本函数只 flush，便于上层把状态回写与片段写入放在同一事务里。
    """
    version = session.get(DocumentVersion, version_id)
    if version is None:
        raise IndexBuildError(f"文档版本不存在: {version_id}")

    document = session.get(Document, version.document_id)
    if document is None:
        raise IndexBuildError(f"文档不存在: {version.document_id}")

    purge_version_chunks(session, version)

    task = _resolve_parse_task(session, version)
    if task is None:
        version.index_status = IndexStatus.SKIPPED.value
        version.index_error = "该版本还没有成功的解析结果"
        version.indexed_at = None
        version.chunk_count = 0
        session.flush()
        return version

    blocks = list(
        session.scalars(
            select(ParseBlock)
            .where(ParseBlock.parse_task_id == task.id)
            .order_by(ParseBlock.seq)
        )
    )
    drafts = build_chunks(blocks, _chunk_config_for(session, task))

    if not drafts:
        version.index_status = IndexStatus.SKIPPED.value
        version.index_error = "解析结果没有可索引的文本块"
        version.indexed_at = datetime.now(UTC)
        version.chunk_count = 0
        session.flush()
        return version

    _apply_glossary(session, task, blocks)

    if not provider.is_embedding_configured():
        # 不阻塞发布：标记 skipped 并保留原因，配置好后可重建
        version.index_status = IndexStatus.SKIPPED.value
        version.index_error = "未配置 embedding（EMBEDDING_API_KEY / EMBEDDING_BASE_URL 为空）"
        version.indexed_at = None
        version.chunk_count = 0
        session.flush()
        return version

    version.index_status = IndexStatus.BUILDING.value
    version.index_error = None
    session.flush()

    try:
        vectors = provider.embed_texts([draft.content for draft in drafts])
    except provider.EmbeddingError as exc:
        version.index_status = IndexStatus.FAILED.value
        version.index_error = str(exc)[:2000]
        version.chunk_count = 0
        version.indexed_at = datetime.now(UTC)
        session.flush()
        raise IndexBuildError(str(exc)) from exc

    _persist_chunks(session, document, task, version, drafts, vectors)
    version.index_status = IndexStatus.READY.value
    version.index_error = None
    version.chunk_count = len(drafts)
    version.indexed_at = datetime.now(UTC)
    session.flush()
    return version


def _persist_chunks(
    session: Session,
    document: Document,
    task: ParseTask,
    version: DocumentVersion,
    drafts: list[ChunkDraft],
    vectors: list[list[float]],
) -> None:
    for draft, vector in zip(drafts, vectors, strict=True):
        session.add(
            DocumentChunk(
                workspace_id=document.workspace_id,
                document_id=document.id,
                document_version_id=version.id,
                parse_task_id=task.id,
                seq=draft.seq,
                content=draft.content,
                heading_path=draft.heading_path,
                block_ids=draft.block_ids,
                first_page_no=draft.first_page_no,
                last_page_no=draft.last_page_no,
                char_count=draft.char_count,
                embedding=vector,
                embedding_model=settings.embedding_model,
            )
        )
    session.flush()
