"""qa records, feedback and triage issues

Revision ID: c8e1a4f92b73
Revises: b3d9e4c71a58
Create Date: 2026-11-23 11:00:00.000000

三件事：建问答留痕与反馈、建问题簇与只追加轨迹、把 ``qa:manage`` 补进
权限目录并授予既有 admin 角色。

权限补授是本文件的重点：``seed.py`` 只影响新库，现网的管理员角色不会因为
枚举新增而自动获得新权限，漏了这一步治理入口就对所有人不可见。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c8e1a4f92b73"
down_revision: str | Sequence[str] | None = "b3d9e4c71a58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QA_PERMISSION = ("qa:manage", "问答治理", "问答", 150)


def upgrade() -> None:
    op.create_table(
        "qa_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_name", sa.String(length=128), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("question_vec", Vector(1024), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_status", sa.String(length=16), nullable=False, server_default="streaming"),
        sa.Column(
            "sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("top_score", sa.Float(), nullable=True),
        sa.Column("last_score", sa.Float(), nullable=True),
        sa.Column(
            "reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
        sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_record_workspace_created", "qa_record", ["workspace_id", "created_at"])
    op.create_index(
        "ix_qa_record_workspace_sensitivity",
        "qa_record",
        ["workspace_id", "sensitivity", "created_at"],
    )
    op.create_index("ix_qa_record_user", "qa_record", ["user_id", "created_at"])
    op.create_index(
        "ix_qa_record_reasons", "qa_record", ["reasons"], postgresql_using="gin"
    )
    op.create_index(
        "ix_qa_record_question_vec",
        "qa_record",
        ["question_vec"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"question_vec": "vector_cosine_ops"},
    )

    op.create_table(
        "qa_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["record_id"], ["qa_record.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("record_id", "user_id", name="uq_qa_feedback_record_user"),
    )

    op.create_table(
        "qa_issue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sensitivity", sa.String(length=16), nullable=False, server_default="public"),
        sa.Column("representative_question", sa.Text(), nullable=False),
        sa.Column("representative_vec", Vector(1024), nullable=True),
        sa.Column("primary_document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"
        ),
        sa.Column("record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dislike_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("disposition", sa.String(length=32), nullable=True),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["primary_document_id"], ["document.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assignee_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_issue_queue", "qa_issue", ["workspace_id", "status", "last_seen_at"])
    op.create_index("ix_qa_issue_document", "qa_issue", ["primary_document_id"])
    op.create_index("ix_qa_issue_reasons", "qa_issue", ["reasons"], postgresql_using="gin")

    op.create_table(
        "qa_issue_record",
        sa.Column("issue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["qa_issue.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["record_id"], ["qa_record.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("issue_id", "record_id"),
        sa.UniqueConstraint("record_id", name="uq_qa_issue_record_record"),
    )
    op.create_index("ix_qa_issue_record_issue", "qa_issue_record", ["issue_id", "linked_at"])

    op.create_table(
        "qa_issue_event",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_name", sa.String(length=128), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["qa_issue.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_issue_event_issue", "qa_issue_event", ["issue_id", "created_at"])

    _grant_qa_permission()


def _grant_qa_permission() -> None:
    code, name, group_name, sort_order = QA_PERMISSION
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO permission (code, name, group_name, sort_order) "
            "VALUES (:code, :name, :group_name, :sort_order) ON CONFLICT DO NOTHING"
        ),
        {"code": code, "name": name, "group_name": group_name, "sort_order": sort_order},
    )
    # 只补内置 admin；reviewer / contributor 需要治理时由运营在角色页显式授予
    connection.execute(
        sa.text(
            "INSERT INTO role_permission (role_id, permission_code) "
            "SELECT id, :code FROM role WHERE code = 'admin' ON CONFLICT DO NOTHING"
        ),
        {"code": code},
    )


def downgrade() -> None:
    code = QA_PERMISSION[0]
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM role_permission WHERE permission_code = :code"), {"code": code}
    )
    connection.execute(sa.text("DELETE FROM permission WHERE code = :code"), {"code": code})

    op.drop_index("ix_qa_issue_event_issue", table_name="qa_issue_event")
    op.drop_table("qa_issue_event")
    op.drop_index("ix_qa_issue_record_issue", table_name="qa_issue_record")
    op.drop_table("qa_issue_record")
    op.drop_index("ix_qa_issue_reasons", table_name="qa_issue")
    op.drop_index("ix_qa_issue_document", table_name="qa_issue")
    op.drop_index("ix_qa_issue_queue", table_name="qa_issue")
    op.drop_table("qa_issue")
    op.drop_table("qa_feedback")
    op.drop_index("ix_qa_record_question_vec", table_name="qa_record")
    op.drop_index("ix_qa_record_reasons", table_name="qa_record")
    op.drop_index("ix_qa_record_user", table_name="qa_record")
    op.drop_index("ix_qa_record_workspace_sensitivity", table_name="qa_record")
    op.drop_index("ix_qa_record_workspace_created", table_name="qa_record")
    op.drop_table("qa_record")
