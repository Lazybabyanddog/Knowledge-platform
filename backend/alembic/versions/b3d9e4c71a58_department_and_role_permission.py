"""department tree, permission catalog and role permissions

Revision ID: b3d9e4c71a58
Revises: a7c3f1b90e42
Create Date: 2026-11-23 10:00:00.000000

三件事：建部门树、把权限目录与角色-权限关系落库、给人员加部门归属。

权限码与内置角色矩阵在本文件内硬编码（不 import 应用枚举），保证历史迁移
可重放；``app/scripts/seed.py`` 只做幂等补齐，不覆盖运营侧的调整。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b3d9e4c71a58"
down_revision: str | Sequence[str] | None = "a7c3f1b90e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (code, name, group_name, sort_order)
PERMISSION_SEEDS: list[tuple[str, str, str, int]] = [
    ("user:manage", "用户管理", "用户", 10),
    ("department:manage", "部门管理", "组织", 20),
    ("role:manage", "角色权限管理", "角色", 30),
    ("audit:read", "审计日志查看", "审计", 40),
    ("document:read", "文档查看", "文档", 50),
    ("document:write", "文档上传", "文档", 60),
    ("document:delete", "文档删除", "文档", 70),
    ("parse:run", "解析执行", "解析", 80),
    ("ontology:read", "本体查看", "本体", 90),
    ("ontology:manage", "本体管理", "本体", 100),
    ("ontology:review", "本体审核", "本体", 110),
    ("graph:read", "图谱查看", "图谱", 120),
    ("review:submit", "审批提交", "审批", 130),
    ("review:approve", "审批通过", "审批", 140),
]

# (code, name, description, permissions)
ROLE_SEEDS: list[tuple[str, str, str, list[str]]] = [
    (
        "admin",
        "管理员",
        "全部权限，含用户管理与审批配置",
        [code for code, *_ in PERMISSION_SEEDS],
    ),
    (
        "reviewer",
        "审批人",
        "审核本体候选与文档审批",
        ["document:read", "ontology:read", "graph:read", "review:approve"],
    ),
    (
        "contributor",
        "贡献者",
        "上传文档、触发解析与抽取",
        [
            "document:read",
            "document:write",
            "document:delete",
            "parse:run",
            "ontology:read",
            "ontology:review",
            "graph:read",
            "review:submit",
        ],
    ),
    (
        "viewer",
        "查看者",
        "只读访问文档与图谱",
        ["document:read", "ontology:read", "graph:read"],
    ),
]


def upgrade() -> None:
    op.create_table(
        "department",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["department.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "parent_id", "name", name="uq_department_workspace_parent_name"
        ),
    )
    op.create_index("ix_department_workspace_id", "department", ["workspace_id"], unique=False)
    op.create_index("ix_department_parent_id", "department", ["parent_id"], unique=False)
    op.create_index(
        "uq_department_workspace_root_name",
        "department",
        ["workspace_id", "name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )

    op.create_table(
        "permission",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("group_name", sa.String(length=32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "role_permission",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_code", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["role.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["permission_code"], ["permission.code"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("role_id", "permission_code"),
    )

    op.add_column("app_user", sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_app_user_department_id",
        "app_user",
        "department",
        ["department_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_app_user_department_id"), "app_user", ["department_id"], unique=False)

    _seed_permissions()
    _seed_roles()


def _seed_permissions() -> None:
    op.bulk_insert(
        sa.table(
            "permission",
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("group_name", sa.String),
            sa.column("sort_order", sa.Integer),
        ),
        [
            {"code": code, "name": name, "group_name": group, "sort_order": order}
            for code, name, group, order in PERMISSION_SEEDS
        ],
    )


def _seed_roles() -> None:
    connection = op.get_bind()
    for code, name, description, permissions in ROLE_SEEDS:
        # 已有同 code 的角色（老库可能先跑过 seed）时保留原记录
        role_id = connection.execute(
            sa.text("SELECT id FROM role WHERE code = :code"), {"code": code}
        ).scalar()
        if role_id is None:
            role_id = uuid.uuid4()
            connection.execute(
                sa.text(
                    "INSERT INTO role (id, code, name, description, is_system) "
                    "VALUES (:id, :code, :name, :description, true)"
                ),
                {"id": role_id, "code": code, "name": name, "description": description},
            )
        for permission_code in permissions:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permission (role_id, permission_code) "
                    "VALUES (:role_id, :permission_code) ON CONFLICT DO NOTHING"
                ),
                {"role_id": role_id, "permission_code": permission_code},
            )


def downgrade() -> None:
    op.drop_index(op.f("ix_app_user_department_id"), table_name="app_user")
    op.drop_constraint("fk_app_user_department_id", "app_user", type_="foreignkey")
    op.drop_column("app_user", "department_id")

    op.drop_table("role_permission")
    op.drop_table("permission")

    op.drop_index("uq_department_workspace_root_name", table_name="department")
    op.drop_index("ix_department_parent_id", table_name="department")
    op.drop_index("ix_department_workspace_id", table_name="department")
    op.drop_table("department")
