"""ORM 模型集合。

Alembic 通过导入本包来发现全部表，新增模型必须在此登记。
"""

from app.infra.db.models.audit_log import AuditLog
from app.infra.db.models.department import Department
from app.infra.db.models.document import Document, DocumentAcl, DocumentVersion
from app.infra.db.models.extraction_backfill import ExtractionBackfillBatch
from app.infra.db.models.extraction_task import (
    ExtractionBatchCheckpoint,
    ExtractionTask,
    ExtractionTaskAttempt,
)
from app.infra.db.models.ontology import OntologyClass, OntologyRelation
from app.infra.db.models.parse_strategy import ParseStrategy
from app.infra.db.models.permission import Permission, RolePermission
from app.infra.db.models.parse_task import ParseBlock, ParseTask, ParseTaskAttempt
from app.infra.db.models.qa import QaFeedback, QaIssue, QaIssueEvent, QaIssueRecord, QaRecord
from app.infra.db.models.refresh_token import RefreshToken
from app.infra.db.models.retrieval import DocumentChunk
from app.infra.db.models.review import ReviewFlow, ReviewInstance, ReviewRecord
from app.infra.db.models.role import Role, UserRole
from app.infra.db.models.user import AppUser
from app.infra.db.models.workspace import Workspace

__all__ = [
    "AppUser",
    "AuditLog",
    "Department",
    "Document",
    "DocumentAcl",
    "DocumentChunk",
    "DocumentVersion",
    "ExtractionBackfillBatch",
    "ExtractionBatchCheckpoint",
    "ExtractionTask",
    "ExtractionTaskAttempt",
    "OntologyClass",
    "OntologyRelation",
    "ParseBlock",
    "ParseStrategy",
    "ParseTask",
    "ParseTaskAttempt",
    "Permission",
    "QaFeedback",
    "QaIssue",
    "QaIssueEvent",
    "QaIssueRecord",
    "QaRecord",
    "RefreshToken",
    "ReviewFlow",
    "ReviewInstance",
    "ReviewRecord",
    "Role",
    "RolePermission",
    "UserRole",
    "Workspace",
]
