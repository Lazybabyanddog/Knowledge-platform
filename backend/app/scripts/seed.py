"""种子数据：默认空间、4 个内置角色、初始管理员、各文档类型默认解析策略。

幂等设计：以业务唯一键（slug / code / username / strategy_key+version）判定是否已存在，
重复执行只补齐缺失项，不覆盖已有数据（尤其不重置管理员密码）。

用法：python -m app.scripts.seed
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password
from app.domain.enums import (
    FileType,
    RoleCode,
    StrategyStatus,
    UserStatus,
    WorkspaceStatus,
)
from app.domain.policies import BUILTIN_ROLE_PERMISSIONS, PERMISSION_CATALOG
from app.infra.db.models import (
    AppUser,
    ParseStrategy,
    Permission as PermissionRow,
)
from app.infra.db.models import Role, RolePermission, UserRole, Workspace
from app.infra.db.session import session_scope

logger = logging.getLogger(__name__)

ROLE_SEEDS: list[tuple[RoleCode, str, str]] = [
    (RoleCode.ADMIN, "管理员", "全部权限，含用户管理与审批配置"),
    (RoleCode.REVIEWER, "审批人", "审核本体候选与文档审批"),
    (RoleCode.CONTRIBUTOR, "贡献者", "上传文档、触发解析与抽取"),
    (RoleCode.VIEWER, "查看者", "只读访问文档与图谱"),
]

# engine 取值与解析适配层的注册 key 对应（见 4.3）。
# Office 走 OOXML 直读；图片走 OCR。
STRATEGY_SEEDS: list[tuple[FileType, str, str, dict[str, Any]]] = [
    (
        FileType.PDF,
        "pdf_layout",
        "PDF 版面解析（默认）",
        {"ocr": False, "remove_header": True, "table_mode": "structured", "chunk_hint": 800},
    ),
    (
        FileType.OFFICE,
        "office",
        "Office 文档解析（默认）",
        {"ocr": False, "remove_header": True, "table_mode": "structured", "chunk_hint": 800},
    ),
    (
        FileType.TEXT,
        "plain_text",
        "纯文本解析（默认）",
        {"ocr": False, "remove_header": False, "chunk_hint": 800},
    ),
    (
        FileType.IMAGE,
        "ocr",
        "图片 OCR 解析（默认）",
        {"ocr": True, "remove_header": False, "chunk_hint": 500},
    ),
    (
        FileType.HTML,
        "html_dom",
        "HTML 结构解析（默认）",
        {
            "ocr": False,
            "remove_header": True,
            "content_selector": "body",
            "merge_titles": True,
            "keep_images": False,
            "chunk_hint": 800,
        },
    ),
]


def ensure_workspace(session: Session) -> tuple[Workspace, bool]:
    workspace = session.scalar(
        select(Workspace).where(Workspace.slug == settings.seed_workspace_slug)
    )
    if workspace is not None:
        return workspace, False

    workspace = Workspace(
        name=settings.seed_workspace_name,
        slug=settings.seed_workspace_slug,
        description="初始化创建，可在管理端修改",
        status=WorkspaceStatus.ACTIVE.value,
    )
    session.add(workspace)
    session.flush()
    return workspace, True


def ensure_roles(session: Session) -> tuple[dict[str, Role], int]:
    existing = {role.code: role for role in session.scalars(select(Role)).all()}
    created = 0
    for code, name, description in ROLE_SEEDS:
        if code.value in existing:
            continue
        role = Role(code=code.value, name=name, description=description, is_system=True)
        session.add(role)
        existing[code.value] = role
        created += 1
    session.flush()
    return existing, created


def ensure_permissions(session: Session) -> tuple[int, int]:
    """补齐权限目录与内置角色的初始授权。

    只补缺失项：已存在的权限不更新，已被运营调整过的内置角色不再覆盖
    （仅当该角色一条授权都没有时才视为"未初始化"并写入初始矩阵）。
    """

    existing_codes = set(session.scalars(select(PermissionRow.code)).all())
    permissions_created = 0
    for meta in PERMISSION_CATALOG:
        code = meta.code.value
        if code in existing_codes:
            continue
        session.add(
            PermissionRow(
                code=code,
                name=meta.name,
                group_name=meta.group,
                sort_order=meta.sort_order,
            )
        )
        existing_codes.add(code)
        permissions_created += 1
    session.flush()

    roles = {role.code: role for role in session.scalars(select(Role)).all()}
    grants_created = 0
    for role_code, permissions in BUILTIN_ROLE_PERMISSIONS.items():
        role = roles.get(role_code)
        if role is None:
            continue
        initialized = session.scalar(
            select(RolePermission.permission_code).where(
                RolePermission.role_id == role.id
            ).limit(1)
        )
        if initialized is not None:
            continue
        for permission in permissions:
            session.add(RolePermission(role_id=role.id, permission_code=permission.value))
            grants_created += 1
    session.flush()
    return permissions_created, grants_created


def ensure_admin(
    session: Session, workspace: Workspace, roles: dict[str, Role]
) -> tuple[AppUser, bool]:
    admin = session.scalar(select(AppUser).where(AppUser.username == settings.seed_admin_username))
    created = False
    if admin is None:
        admin = AppUser(
            workspace_id=workspace.id,
            username=settings.seed_admin_username,
            email=None,
            password_hash=hash_password(settings.seed_admin_password),
            display_name=settings.seed_admin_display_name,
            status=UserStatus.ACTIVE.value,
        )
        session.add(admin)
        session.flush()
        created = True

    admin_role = roles[RoleCode.ADMIN.value]
    bound = session.get(UserRole, {"user_id": admin.id, "role_id": admin_role.id})
    if bound is None:
        session.add(UserRole(user_id=admin.id, role_id=admin_role.id))
        session.flush()

    return admin, created


def ensure_strategies(session: Session, workspace: Workspace, created_by: Any) -> int:
    created = 0
    for doc_type, engine, name, config in STRATEGY_SEEDS:
        strategy_key = f"{doc_type.value}-default"
        exists = session.scalar(
            select(ParseStrategy).where(
                ParseStrategy.strategy_key == strategy_key,
                ParseStrategy.version == 1,
            )
        )
        if exists is not None:
            continue

        session.add(
            ParseStrategy(
                workspace_id=workspace.id,
                strategy_key=strategy_key,
                version=1,
                name=name,
                doc_type=doc_type.value,
                engine=engine,
                config=config,
                is_default=True,
                status=StrategyStatus.ACTIVE.value,
                created_by=created_by,
            )
        )
        session.flush()
        created += 1
    return created


def run(session: Session) -> dict[str, object]:
    workspace, workspace_created = ensure_workspace(session)
    roles, roles_created = ensure_roles(session)
    permissions_created, grants_created = ensure_permissions(session)
    admin, admin_created = ensure_admin(session, workspace, roles)
    strategies_created = ensure_strategies(session, workspace, admin.id)
    return {
        "workspace": workspace.slug,
        "workspace_created": workspace_created,
        "roles_created": roles_created,
        "permissions_created": permissions_created,
        "grants_created": grants_created,
        "admin": admin.username,
        "admin_created": admin_created,
        "strategies_created": strategies_created,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with session_scope() as session:
        result = run(session)

    print(
        "seed done: "
        f"workspace={result['workspace']}(created={result['workspace_created']}) "
        f"roles_created={result['roles_created']} "
        f"permissions_created={result['permissions_created']} "
        f"grants_created={result['grants_created']} "
        f"admin={result['admin']}(created={result['admin_created']}) "
        f"strategies_created={result['strategies_created']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
