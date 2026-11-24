"""向量召回。

过滤条件（workspace / 发布态 / 未删除 / 当前版本 / 密级）与向量排序写在同一条 SQL 里：
先召回再在应用层筛，会把不该看到的片段先读出来，也会因为过滤掉大量候选而召回不足。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.domain.enums import AuditAction, DocumentStatus
from app.domain.visibility import allowed_sensitivities
from app.infra.db.models.document import Document, DocumentVersion
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.llm import provider
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class RetrievalUnavailable(RuntimeError):
    """检索能力不可用（未配置 embedding 等），接口层应返回 503。"""


@dataclasses.dataclass(frozen=True, slots=True)
class ChunkHit:
    chunk_id: str
    document_id: str
    title: str
    version_no: int
    content: str
    score: float
    heading_path: str | None
    first_page_no: int | None
    last_page_no: int | None
    block_ids: list[str]


class RetrievalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.audit = AuditService(db)

    def search(
        self,
        principal: Principal,
        question: str,
        *,
        top_k: int | None = None,
        doc_type: str | None = None,
    ) -> list[ChunkHit]:
        query = (question or "").strip()
        if not query:
            return []
        return self.search_many(principal, [query], top_k=top_k, doc_type=doc_type)[query]

    def search_with_vector(
        self,
        principal: Principal,
        question: str,
        *,
        top_k: int | None = None,
        doc_type: str | None = None,
    ) -> tuple[list[ChunkHit], list[float]]:
        """单句检索，同时把问题向量交回调用方。

        问答落痕需要问题向量做缺口聚类；``search_many`` 只为批量改写服务，
        不返回向量，若在这里再调一次 embedding 就白付一次模型成本。
        过滤条件仍全部来自 ``_base_stmt``，与检索口径不会分叉。
        """
        query = (question or "").strip()
        if not query:
            return [], []
        if not provider.is_embedding_configured():
            raise RetrievalUnavailable("未配置 embedding 模型，无法检索")
        try:
            vector = provider.embed_texts([query])[0]
        except provider.EmbeddingError as exc:
            raise RetrievalUnavailable(f"问题向量化失败: {exc}") from exc

        limit = top_k or settings.retrieval_top_k
        self._tune_hnsw()
        stmt = self._base_stmt(principal, vector, doc_type=doc_type, limit=limit)
        hits = [self._to_hit(row) for row in self.db.execute(stmt).all()][:limit]
        return hits, list(vector)

    def search_many(
        self,
        principal: Principal,
        queries: list[str],
        *,
        top_k: int | None = None,
        doc_type: str | None = None,
    ) -> dict[str, list[ChunkHit]]:
        """批量检索：一次向量化，每个查询一条语句。

        改写链路一次要发 N 条查询，逐条调用 ``search`` 会付 N 次向量化成本；
        而过滤条件必须完全一致，所以语句统一由 ``_base_stmt`` 生成。
        """
        ordered: list[str] = []
        for raw in queries:
            text = (raw or "").strip()
            if text and text not in ordered:
                ordered.append(text)
        if not ordered:
            return {}
        if not provider.is_embedding_configured():
            raise RetrievalUnavailable("未配置 embedding 模型，无法检索")

        try:
            vectors = provider.embed_texts(ordered)
        except provider.EmbeddingError as exc:
            raise RetrievalUnavailable(f"问题向量化失败: {exc}") from exc

        limit = top_k or settings.retrieval_top_k
        # HNSW 在过滤条件下可能召回不足，先过采样，再截断
        self._tune_hnsw()

        result: dict[str, list[ChunkHit]] = {}
        for text, vector in zip(ordered, vectors, strict=True):
            stmt = self._base_stmt(principal, vector, doc_type=doc_type, limit=limit)
            result[text] = [self._to_hit(row) for row in self.db.execute(stmt).all()][:limit]
        return result

    def _base_stmt(
        self,
        principal: Principal,
        vector: list[float],
        *,
        doc_type: str | None,
        limit: int,
    ) -> Select[Any]:
        """过滤条件与向量排序的唯一来源：对照组与改写组只能查询文本不同。"""
        oversample = max(1, settings.retrieval_oversample)
        distance = DocumentChunk.embedding.cosine_distance(vector)
        stmt: Select[Any] = (
            select(
                DocumentChunk,
                Document.title,
                DocumentVersion.version_no,
                distance.label("distance"),
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .where(
                Document.workspace_id == principal.user.workspace_id,
                Document.status == DocumentStatus.PUBLISHED.value,
                Document.deleted_at.is_(None),
                Document.sensitivity.in_(allowed_sensitivities(principal)),
                DocumentVersion.is_current.is_(True),
                DocumentChunk.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(limit * oversample)
        )
        if doc_type:
            stmt = stmt.where(Document.doc_type == doc_type)
        return stmt

    @staticmethod
    def _to_hit(row: Any) -> ChunkHit:
        chunk, title, version_no, distance_value = row
        return ChunkHit(
            chunk_id=str(chunk.id),
            document_id=str(chunk.document_id),
            title=title,
            version_no=version_no,
            content=chunk.content,
            score=round(1.0 - float(distance_value), 6),
            heading_path=chunk.heading_path,
            first_page_no=chunk.first_page_no,
            last_page_no=chunk.last_page_no,
            block_ids=list(chunk.block_ids or []),
        )

    def _tune_hnsw(self) -> None:
        """只影响当前事务；参数来自配置，转成整数后拼接，避免绑定参数。"""
        ef_search = int(settings.retrieval_hnsw_ef_search)
        if ef_search <= 0:
            return
        self.db.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))

    def record_ask(
        self,
        principal: Principal,
        question: str,
        hit_count: int,
        *,
        ip: str | None = None,
    ) -> None:
        """问答审计：只留问题摘要与命中数，不留答案正文。"""
        self.audit.record(
            AuditAction.RETRIEVAL_ASK,
            actor=principal.user,
            object_type="retrieval",
            after={"question": question[:100], "hits": hit_count},
            ip=ip,
        )


NO_HIT_ANSWER = "知识库中未找到与问题相关的内容。"

_SYSTEM_PROMPT = (
    "你是企业知识库问答助手。只能依据【资料】回答问题，不得使用资料之外的知识，"
    "不得编造事实、数字或条款。资料不足以回答时，直接说明知识库中未找到相关内容；"
    "引用资料时在相关句子末尾标注编号，如 [1]，编号必须来自资料列表。"
)


def build_ask_messages(question: str, hits: list[ChunkHit]) -> list[dict[str, str]]:
    """把召回片段拼成上下文；编号与 SSE 的 sources 顺序一致。"""
    context = "\n\n".join(
        f"[{index}] 《{hit.title}》第 {hit.first_page_no or '-'} 页\n{hit.content}"
        for index, hit in enumerate(hits, start=1)
    )
    user = (
        f"【资料】\n{context}\n\n【问题】\n{question}\n\n"
        "请用中文回答，并在引用处标注 [编号]；资料没有覆盖的部分请明确说明。"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def hit_to_source(hit: ChunkHit, index: int) -> dict[str, Any]:
    """SSE / HTTP 响应里的引用结构，编号从 1 开始与答案中的标注对应。"""
    return {
        "index": index,
        "chunk_id": hit.chunk_id,
        "document_id": hit.document_id,
        "title": hit.title,
        "version_no": hit.version_no,
        "page_no": hit.first_page_no,
        "page_range": [hit.first_page_no, hit.last_page_no]
        if hit.first_page_no is not None and hit.last_page_no is not None
        else None,
        "heading_path": hit.heading_path,
        "score": hit.score,
        "snippet": hit.content[:400],
    }



