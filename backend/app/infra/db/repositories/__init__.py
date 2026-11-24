"""仓储层：每张表一个仓储，收敛查询与写操作，service 不直接拼 SQL。"""

from app.infra.db.repositories.backfill_repository import ExtractionBackfillRepository
from app.infra.db.repositories.department_repository import DepartmentRepository
from app.infra.db.repositories.document_repository import DocumentRepository
from app.infra.db.repositories.extraction_repository import (
    ExtractionCheckpointRepository,
    ExtractionTaskRepository,
)
from app.infra.db.repositories.ontology_repository import OntologyRepository
from app.infra.db.repositories.parse_repository import (
    ParseBlockRepository,
    ParseStrategyRepository,
    ParseTaskRepository,
)
from app.infra.db.repositories.refresh_token_repository import RefreshTokenRepository
from app.infra.db.repositories.review_repository import ReviewRepository
from app.infra.db.repositories.role_repository import RoleRepository
from app.infra.db.repositories.user_repository import UserRepository

__all__ = [
    "DepartmentRepository",
    "DocumentRepository",
    "ExtractionBackfillRepository",
    "ExtractionCheckpointRepository",
    "ExtractionTaskRepository",
    "OntologyRepository",
    "ParseBlockRepository",
    "ParseStrategyRepository",
    "ParseTaskRepository",
    "RefreshTokenRepository",
    "ReviewRepository",
    "RoleRepository",
    "UserRepository",
]
