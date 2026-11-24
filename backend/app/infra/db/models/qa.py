"""问答留痕与低置信度治理。

与 ``audit_log`` 刻意分开：审计只记问题摘要与命中数，而治理需要完整答案、
来源快照与逐条用户评价。记录带密级并按保留期存放，是"另建表"而非"放开审计"。

治理的主对象是问题簇 ``qa_issue``，``qa_record`` 只是成员与证据：
同一个人反复问、三十个人问同一个问题，都会被归并到一个簇上处理。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIM = 1024


class QaRecord(UUIDPrimaryKeyMixin, Base):
    """一次问答的完整留痕，只增不改（终态回填是唯一例外）。"""

    __tablename__ = "qa_record"
    __table_args__ = (
        Index("ix_qa_record_workspace_created", "workspace_id", "created_at"),
        Index("ix_qa_record_workspace_sensitivity", "workspace_id", "sensitivity", "created_at"),
        Index("ix_qa_record_user", "user_id", "created_at"),
        # 治理端按触发原因筛选，JSONB 数组用 GIN 才走得动
        Index("ix_qa_record_reasons", "reasons", postgresql_using="gin"),
        Index(
            "ix_qa_record_question_vec",
            "question_vec",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"question_vec": "vector_cosine_ops"},
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    # 提问人名字快照：用户被删后仍要能说清"这是谁问的"
    user_name: Mapped[str | None] = mapped_column(String(128))

    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_vec: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    answer_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="streaming", server_default="streaming"
    )
    # 本次来源快照：编号 / 文档 / 片段 / 标题 / 页号 / 分数 / 文档密级
    sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    top_score: Mapped[float | None] = mapped_column(Float)
    last_score: Mapped[float | None] = mapped_column(Float)
    # 自动原因 + 用户原因，两者分开重算（用户原因不参与阈值重算）
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    sensitivity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public", server_default="public"
    )
    elapsed_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 不用 TimestampMixin：记录只增不改，updated_at 没有意义
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class QaFeedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户评价；一人一条，重复提交即覆盖。"""

    __tablename__ = "qa_feedback"
    __table_args__ = (UniqueConstraint("record_id", "user_id", name="uq_qa_feedback_record_user"),)

    record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_record.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)


class QaIssue(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """待处置的问题簇，治理端真正操作的对象。"""

    __tablename__ = "qa_issue"
    __table_args__ = (
        Index("ix_qa_issue_queue", "workspace_id", "status", "last_seen_at"),
        Index("ix_qa_issue_document", "primary_document_id"),
        Index("ix_qa_issue_reasons", "reasons", postgresql_using="gin"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workspace.id", ondelete="CASCADE"), nullable=False
    )
    sensitivity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public", server_default="public"
    )
    representative_question: Mapped[str] = mapped_column(Text, nullable=False)
    # 归簇用代表向量：固定为首条记录的问法，避免每次并入都重算中心
    representative_vec: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    primary_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("document.id", ondelete="SET NULL")
    )
    # 成员原因并集，列表展示与筛选都读这一列，不必回表聚合
    reasons: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 冗余一列"含点踩的成员数"：默认排序要先看点踩，实时聚合会拖慢列表
    dislike_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    disposition: Mapped[str | None] = mapped_column(String(32))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )


class QaIssueRecord(Base):
    """簇与记录的多对一关系；``record_id`` 唯一保证一条记录只属于一个簇。"""

    __tablename__ = "qa_issue_record"
    __table_args__ = (
        UniqueConstraint("record_id", name="uq_qa_issue_record_record"),
        Index("ix_qa_issue_record_issue", "issue_id", "linked_at"),
    )

    issue_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_issue.id", ondelete="CASCADE"), primary_key=True
    )
    record_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_record.id", ondelete="CASCADE"), primary_key=True
    )
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class QaIssueEvent(UUIDPrimaryKeyMixin, Base):
    """只追加的处置轨迹，与 ``review_record`` 同形。"""

    __tablename__ = "qa_issue_event"
    __table_args__ = (Index("ix_qa_issue_event_issue", "issue_id", "created_at"),)

    issue_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("qa_issue.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    actor_name: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(Text)
    # 动作后的关键状态快照（结论 / 处理人），回看轨迹时不用自己推演
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
