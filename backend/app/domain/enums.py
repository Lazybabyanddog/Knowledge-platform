"""状态与角色枚举，取值与数据库、前端一一对应。

除 ``OntologyStatus`` 用 IntEnum（整型 0/1）外，其余继承 StrEnum，
序列化到 JSON 即字符串，无需额外转换。
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


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


class OntologyStatus(IntEnum):
    """本体定义（类/关系类型）的启用状态；失效后不再参与抽取约束。

    取值为整型：0 有效、1 失效，与数据库 ``smallint`` 列一一对应。
    """

    VALID = 0
    INVALID = 1


class AuditAction(StrEnum):
    """审计动作，命名按 ``领域.动作`` 收敛，便于按前缀筛选。"""

    LOGIN_SUCCESS = "login.success"
    LOGIN_FAILURE = "login.failure"
    LOGOUT = "login.logout"

    USER_CREATE = "user.create"
    USER_UPDATE = "user.update"
    USER_RESET_PASSWORD = "user.reset_password"

    DEPARTMENT_CREATE = "department.create"
    DEPARTMENT_UPDATE = "department.update"
    DEPARTMENT_DELETE = "department.delete"

    ROLE_CREATE = "role.create"
    ROLE_UPDATE = "role.update"
    ROLE_DELETE = "role.delete"
    ROLE_PERMISSION_UPDATE = "role.permission_update"

    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_DOWNLOAD = "document.download"
    DOCUMENT_UPDATE = "document.update"
    DOCUMENT_DELETE = "document.delete"
    DOCUMENT_RESTORE = "document.restore"

    PARSE_TRIGGER = "parse.trigger"
    PARSE_RETRY = "parse.retry"
    PARSE_CANCEL = "parse.cancel"
    PARSE_TASK_DELETE = "parse.task_delete"
    PARSE_STRATEGY_CREATE = "parse.strategy_create"
    PARSE_STRATEGY_UPDATE = "parse.strategy_update"
    PARSE_STRATEGY_DELETE = "parse.strategy_delete"

    ONTOLOGY_CLASS_CREATE = "ontology.class_create"
    ONTOLOGY_CLASS_UPDATE = "ontology.class_update"
    ONTOLOGY_CLASS_DELETE = "ontology.class_delete"
    ONTOLOGY_RELATION_CREATE = "ontology.relation_create"
    ONTOLOGY_RELATION_UPDATE = "ontology.relation_update"
    ONTOLOGY_RELATION_DELETE = "ontology.relation_delete"

    EXTRACTION_TRIGGER = "extraction.trigger"
    EXTRACTION_RETRY = "extraction.retry"
    EXTRACTION_CANCEL = "extraction.cancel"
    EXTRACTION_BACKFILL = "extraction.backfill"
    ENTITY_UPDATE = "entity.update"
    ENTITY_CONFIRM = "entity.confirm"
    ENTITY_REJECT = "entity.reject"
    ENTITY_MERGE = "entity.merge"
    ENTITY_MERGE_REVERT = "entity.merge_revert"
    RELATION_CONFIRM = "relation.confirm"
    RELATION_REJECT = "relation.reject"
    RELATION_UPDATE = "relation.update"

    REVIEW_SUBMIT = "review.submit"
    REVIEW_APPROVE = "review.approve"
    REVIEW_REJECT = "review.reject"
    REVIEW_WITHDRAW = "review.withdraw"
    REVIEW_FLOW_DELETE = "review.flow_delete"

    RETRIEVAL_SEARCH = "retrieval.search"
    RETRIEVAL_ASK = "retrieval.ask"

    QA_FEEDBACK = "qa.feedback"
    QA_ISSUE_ASSIGN = "qa.issue_assign"
    QA_ISSUE_DISPOSE = "qa.issue_dispose"
    QA_ISSUE_CLOSE = "qa.issue_close"
    QA_ISSUE_REOPEN = "qa.issue_reopen"


class QaAnswerStatus(StrEnum):
    """一次问答的终态；``STREAMING`` 表示流还在进行或进程中途退出留下的孤儿。"""

    STREAMING = "streaming"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class QaReason(StrEnum):
    """低置信判定的触发原因。

    前三个是硬信号（一旦命中即进入治理队列）；后三个只是打标，
    用于在队列里辅助排序与筛选，不单独构成待处置记录。
    """

    NO_HIT = "no_hit"
    NO_CITATION = "no_citation"
    USER_DISLIKE = "user_dislike"

    FEW_HITS = "few_hits"
    LOW_SCORE = "low_score"
    SCORE_GAP = "score_gap"


HARD_QA_REASONS: frozenset[str] = frozenset(
    {QaReason.NO_HIT.value, QaReason.NO_CITATION.value, QaReason.USER_DISLIKE.value}
)


class QaRating(StrEnum):
    LIKE = "like"
    DISLIKE = "dislike"


class QaDislikeReason(StrEnum):
    """点踩的原因标签，取值固定在契约里，界面按同样顺序展示。"""

    OFF_TOPIC = "off_topic"
    FAKE_CITATION = "fake_citation"
    OUTDATED = "outdated"
    INCOMPLETE = "incomplete"
    OTHER = "other"


class QaIssueStatus(StrEnum):
    PENDING = "pending"
    CLOSED = "closed"


class QaDisposition(StrEnum):
    """处置结论；关闭必须给出其中一个。"""

    FALSE_POSITIVE = "false_positive"
    KNOWLEDGE_GAP = "knowledge_gap"
    RETRIEVAL_FAILURE = "retrieval_failure"
    ANSWER_ERROR = "answer_error"


class QaIssueAction(StrEnum):
    ASSIGN = "assign"
    DISPOSE = "dispose"
    CLOSE = "close"
    REOPEN = "reopen"


class FileType(StrEnum):
    """上传文件的粗分类，用于解析策略路由。"""

    PDF = "pdf"
    OFFICE = "office"
    TEXT = "text"
    IMAGE = "image"
    HTML = "html"
