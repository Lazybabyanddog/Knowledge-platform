"""权限矩阵：角色 -> 权限集合。

单一事实来源。接口层用 ``require_permission`` 声明能力要求，
服务层在需要"仅本人可操作"时再用归属校验做二次判断。
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from app.domain.enums import RoleCode


class Permission(StrEnum):
    USER_MANAGE = "user:manage"
    AUDIT_READ = "audit:read"

    DOCUMENT_READ = "document:read"
    DOCUMENT_WRITE = "document:write"
    DOCUMENT_DELETE = "document:delete"

    PARSE_RUN = "parse:run"

    ONTOLOGY_READ = "ontology:read"
    ONTOLOGY_MANAGE = "ontology:manage"
    ONTOLOGY_REVIEW = "ontology:review"

    GRAPH_READ = "graph:read"

    REVIEW_SUBMIT = "review:submit"
    REVIEW_APPROVE = "review:approve"


_ADMIN = frozenset(Permission)

_REVIEWER = frozenset(
    {
        Permission.DOCUMENT_READ,
        Permission.ONTOLOGY_READ,
        Permission.GRAPH_READ,
        Permission.REVIEW_APPROVE,
    }
)

# 知识治理：贡献者负责确认/合并抽取结果，审批人只做流程审批（见 auth spec 权限矩阵）
_CONTRIBUTOR = frozenset(
    {
        Permission.DOCUMENT_READ,
        Permission.DOCUMENT_WRITE,
        Permission.DOCUMENT_DELETE,
        Permission.PARSE_RUN,
        Permission.ONTOLOGY_READ,
        Permission.ONTOLOGY_REVIEW,
        Permission.GRAPH_READ,
        Permission.REVIEW_SUBMIT,
    }
)

_VIEWER = frozenset(
    {
        Permission.DOCUMENT_READ,
        Permission.ONTOLOGY_READ,
        Permission.GRAPH_READ,
    }
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    RoleCode.ADMIN.value: _ADMIN,
    RoleCode.REVIEWER.value: _REVIEWER,
    RoleCode.CONTRIBUTOR.value: _CONTRIBUTOR,
    RoleCode.VIEWER.value: _VIEWER,
}


def permissions_for(roles: Iterable[str]) -> frozenset[Permission]:
    granted: set[Permission] = set()
    for role in roles:
        granted |= ROLE_PERMISSIONS.get(role, frozenset())
    return frozenset(granted)


def has_permission(roles: Iterable[str], permission: Permission) -> bool:
    return permission in permissions_for(roles)
