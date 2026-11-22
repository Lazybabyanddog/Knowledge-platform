"""状态与角色枚举，取值与数据库、前端一一对应。

全部继承 StrEnum，序列化到 JSON 即字符串，无需额外转换。
"""

from __future__ import annotations

from enum import StrEnum


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class WorkspaceStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RoleCode(StrEnum):
    """4 个内置角色，code 即权限矩阵的键。"""

    ADMIN = "admin"
    REVIEWER = "reviewer"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    PENDING = "pending"
    PUBLISHED = "published"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class SourceType(StrEnum):
    UPLOAD = "upload"
    URL = "url"
    IMPORT = "import"


class Sensitivity(StrEnum):
    """密级，越往下越敏感。"""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    SECRET = "secret"


class ParseTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IndexStatus(StrEnum):
    """文档版本的检索索引状态。"""

    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExtractionTaskStatus(StrEnum):
    """与解析任务同构，独立声明以便两者将来各自演进。"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EntityStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    MERGED = "merged"


class ReviewInstanceStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    WITHDRAW = "withdraw"


class BlockType(StrEnum):
    """解析产物块的类型。"""

    TITLE = "title"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    IMAGE = "image"
    HEADER = "header"
    FOOTER = "footer"
    CODE = "code"


class StrategyStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class OntologyStatus(StrEnum):
    """本体定义（类/关系类型）的启用状态；禁用后不再参与抽取约束。"""

    ACTIVE = "active"
    DISABLED = "disabled"


class AuditAction(StrEnum):
    """审计动作，命名按 ``领域.动作`` 收敛，便于按前缀筛选。"""

    LOGIN_SUCCESS = "login.success"
    LOGIN_FAILURE = "login.failure"
    LOGOUT = "login.logout"

    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_RESET_PASSWORD = "user.reset_password"

    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_DOWNLOAD = "document.download"
    DOCUMENT_UPDATE = "document.update"
    DOCUMENT_DELETE = "document.delete"
    DOCUMENT_RESTORE = "document.restore"

    PARSE_TRIGGER = "parse.trigger"
    PARSE_RETRY = "parse.retry"
    PARSE_CANCEL = "parse.cancel"
    PARSE_STRATEGY_CREATE = "parse.strategy_create"
    PARSE_STRATEGY_UPDATE = "parse.strategy_update"

    ONTOLOGY_CLASS_CREATE = "ontology.class_create"
    ONTOLOGY_CLASS_UPDATE = "ontology.class_update"
    ONTOLOGY_RELATION_CREATE = "ontology.relation_create"
    ONTOLOGY_RELATION_UPDATE = "ontology.relation_update"

    EXTRACTION_TRIGGER = "extraction.trigger"
    EXTRACTION_RETRY = "extraction.retry"
    EXTRACTION_CANCEL = "extraction.cancel"
    ENTITY_UPDATE = "entity.update"
    ENTITY_CONFIRM = "entity.confirm"
    ENTITY_REJECT = "entity.reject"
    ENTITY_MERGE = "entity.merge"
    ENTITY_MERGE_REVERT = "entity.merge_revert"
    RELATION_CONFIRM = "relation.confirm"
    RELATION_REJECT = "relation.reject"

    REVIEW_SUBMIT = "review.submit"
    REVIEW_APPROVE = "review.approve"
    REVIEW_REJECT = "review.reject"
    REVIEW_WITHDRAW = "review.withdraw"

    RETRIEVAL_SEARCH = "retrieval.search"
    RETRIEVAL_ASK = "retrieval.ask"


class FileType(StrEnum):
    """上传文件的粗分类，用于解析策略路由。"""

    PDF = "pdf"
    OFFICE = "office"
    TEXT = "text"
    IMAGE = "image"
    HTML = "html"
