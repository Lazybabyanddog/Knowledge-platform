"""权限清单与内置角色的初始权限。

运行时判定以数据库中的 ``role_permission`` 为准（见 ``core/deps.py``）；
本模块的 ``Permission`` 是可判定能力的清单，``BUILTIN_ROLE_PERMISSIONS`` 与
``PERMISSION_CATALOG`` 只作为迁移/种子数据的来源，不再参与请求期计算。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import RoleCode


class Permission(StrEnum):
    USER_MANAGE = "user:manage"
    DEPARTMENT_MANAGE = "department:manage"
    ROLE_MANAGE = "role:manage"
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

    QA_MANAGE = "qa:manage"


@dataclass(frozen=True)
class PermissionMeta:
    """权限目录的一行：编码、中文名、分组与展示顺序。"""

    code: Permission
    name: str
    group: str
    sort_order: int


PERMISSION_CATALOG: tuple[PermissionMeta, ...] = (
    PermissionMeta(Permission.USER_MANAGE, "用户管理", "用户", 10),
    PermissionMeta(Permission.DEPARTMENT_MANAGE, "部门管理", "组织", 20),
    PermissionMeta(Permission.ROLE_MANAGE, "角色权限管理", "角色", 30),
    PermissionMeta(Permission.AUDIT_READ, "审计日志查看", "审计", 40),
    PermissionMeta(Permission.DOCUMENT_READ, "文档查看", "文档", 50),
    PermissionMeta(Permission.DOCUMENT_WRITE, "文档上传", "文档", 60),
    PermissionMeta(Permission.DOCUMENT_DELETE, "文档删除", "文档", 70),
    PermissionMeta(Permission.PARSE_RUN, "解析执行", "解析", 80),
    PermissionMeta(Permission.ONTOLOGY_READ, "本体查看", "本体", 90),
    PermissionMeta(Permission.ONTOLOGY_MANAGE, "本体管理", "本体", 100),
    PermissionMeta(Permission.ONTOLOGY_REVIEW, "本体审核", "本体", 110),
    PermissionMeta(Permission.GRAPH_READ, "图谱查看", "图谱", 120),
    PermissionMeta(Permission.REVIEW_SUBMIT, "审批提交", "审批", 130),
    PermissionMeta(Permission.REVIEW_APPROVE, "审批通过", "审批", 140),
    PermissionMeta(Permission.QA_MANAGE, "问答治理", "问答", 150),
)


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

# 内置角色的初始权限；运营在角色管理里调整后，seed 不会覆盖
BUILTIN_ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    RoleCode.ADMIN.value: _ADMIN,
    RoleCode.REVIEWER.value: _REVIEWER,
    RoleCode.CONTRIBUTOR.value: _CONTRIBUTOR,
    RoleCode.VIEWER.value: _VIEWER,
}
