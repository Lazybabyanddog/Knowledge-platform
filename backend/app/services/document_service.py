"""文档服务：上传、版本、下载、元数据维护。

写入顺序固定为"先校验、再算哈希、再进对象存储、最后落库"，任一环节失败都回滚
并删除已上传对象，避免出现"库里有记录、存储里没文件"或相反的不一致。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import Principal
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.domain import state_machines
from app.domain.enums import AuditAction, DocumentStatus, Sensitivity
from app.domain.files import (
    HEAD_BYTES,
    FileProfile,
    ensure_content_matches,
    ensure_size,
    profile_of,
)
from app.infra.db.models import AppUser, Document, DocumentVersion
from app.infra.db.repositories import DocumentRepository
from app.infra.storage import get_storage, stream_checksum
from app.schemas.document import (
    DocumentItem,
    DocumentListResponse,
    DocumentQuery,
    DocumentUpdateRequest,
    DocumentVersionItem,
    DownloadUrl,
)
from app.services.audit_service import AuditService
from app.services.index_service import enqueue_version_index, reset_version_index

logger = logging.getLogger(__name__)

PRESIGN_MAX_SECONDS = 900


@dataclass(slots=True)
class DocumentMeta:
    title: str | None = None
    summary: str | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    tags: list[str] = field(default_factory=list)
    extra_metadata: dict[str, Any] = field(default_factory=dict)


def version_item(version: DocumentVersion) -> DocumentVersionItem:
    return DocumentVersionItem(
        id=str(version.id),
        document_id=str(version.document_id),
        version_no=version.version_no,
        file_name=version.file_name,
        file_size=version.file_size,
        mime_type=version.mime_type,
        checksum=version.checksum,
        page_count=version.page_count,
        is_current=version.is_current,
        change_note=version.change_note,
        index_status=version.index_status,
        index_error=version.index_error,
        chunk_count=version.chunk_count,
        indexed_at=version.indexed_at,
        created_by=str(version.created_by) if version.created_by else None,
        created_at=version.created_at,
    )


def document_item(
    document: Document, current_version: DocumentVersion | None = None
) -> DocumentItem:
    return DocumentItem(
        id=str(document.id),
        title=document.title,
        summary=document.summary,
        source_type=document.source_type,
        doc_type=document.doc_type,
        status=document.status,
        current_version_id=(
            str(document.current_version_id) if document.current_version_id else None
        ),
        owner_id=str(document.owner_id) if document.owner_id else None,
        sensitivity=document.sensitivity,
        tags=list(document.tags or []),
        extra_metadata=dict(document.extra_metadata or {}),
        deleted_at=document.deleted_at,
        created_by=str(document.created_by) if document.created_by else None,
        created_at=document.created_at,
        updated_at=document.updated_at,
        current_version=version_item(current_version) if current_version else None,
    )


def _snapshot(document: Document) -> dict[str, Any]:
    return {
        "title": document.title,
        "summary": document.summary,
        "status": document.status,
        "sensitivity": document.sensitivity,
        "tags": list(document.tags or []),
        "extra_metadata": dict(document.extra_metadata or {}),
        "owner_id": str(document.owner_id) if document.owner_id else None,
    }


class DocumentService:
    def __init__(self, db: Session, storage: Any | None = None) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.storage = storage or get_storage()
        self.audit = AuditService(db)

    # ------------------------------------------------------------------ #
    # 上传
    # ------------------------------------------------------------------ #
    def create_document(
        self,
        principal: Principal,
        upload: UploadFile,
        meta: DocumentMeta,
        *,
        ip: str | None = None,
    ) -> tuple[Document, DocumentVersion]:
        profile, size, checksum = self._inspect(upload)
        title = (meta.title or self._stem(profile.file_name)).strip()[:255]

        document = Document(
            id=uuid.uuid4(),
            workspace_id=principal.user.workspace_id,
            title=title,
            summary=meta.summary,
            doc_type=profile.doc_type,
            status=DocumentStatus.DRAFT.value,
            owner_id=principal.user_id,
            sensitivity=meta.sensitivity.value,
            tags=meta.tags,
            extra_metadata=meta.extra_metadata,
            created_by=principal.user_id,
        )
        version = DocumentVersion(
            id=uuid.uuid4(),
            document_id=document.id,
            version_no=1,
            storage_key="",
            file_name=profile.file_name,
            file_size=size,
            mime_type=profile.mime_type,
            checksum=checksum,
            is_current=True,
            created_by=principal.user_id,
        )
        version.storage_key = self._storage_key(document, version, profile)
        document.current_version_id = version.id

        self._put_object(version, upload)
        try:
            self.documents.add(document)
            self.db.flush()
            self.documents.add_version(version)
            self.db.flush()
            self.audit.record(
                AuditAction.DOCUMENT_UPLOAD,
                actor=principal.user,
                object_type="document",
                object_id=document.id,
                after={
                    "title": document.title,
                    "doc_type": document.doc_type,
                    "version_no": version.version_no,
                    "file_size": version.file_size,
                },
                ip=ip,
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.storage.delete(version.storage_key)
            raise

        return document, version

    def add_version(
        self,
        principal: Principal,
        document_id: uuid.UUID,
        upload: UploadFile,
        *,
        change_note: str | None = None,
        ip: str | None = None,
    ) -> tuple[Document, DocumentVersion]:
        document = self._get_or_404(document_id, principal.user.workspace_id)
        if document.status == DocumentStatus.PENDING.value:
            # 审批中版本冻结：避免"审批的是旧文件、发布的是新文件"
            raise ConflictError(
                "文档审批中，版本已冻结；请先撤回或等待审批结束",
                data={"status": document.status},
            )
        profile, size, checksum = self._inspect(upload)

        if self.documents.checksum_exists(document.id, checksum):
            raise ConflictError(
                "文件内容与已有版本重复",
                data={"checksum": checksum, "document_id": str(document.id)},
            )

        version = DocumentVersion(
            id=uuid.uuid4(),
            document_id=document.id,
            version_no=self.documents.max_version_no(document.id) + 1,
            storage_key="",
            file_name=profile.file_name,
            file_size=size,
            mime_type=profile.mime_type,
            checksum=checksum,
            # 新版本默认草稿，不顶替当前生效版本
            is_current=False,
            change_note=change_note,
            created_by=principal.user_id,
        )
        version.storage_key = self._storage_key(document, version, profile)
        self._put_object(version, upload)

        try:
            self.documents.add_version(version)
            self.db.flush()
            self.audit.record(
                AuditAction.DOCUMENT_UPDATE,
                actor=principal.user,
                object_type="document_version",
                object_id=version.id,
                after={
                    "document_id": str(document.id),
                    "version_no": version.version_no,
                    "change_note": change_note,
                },
                ip=ip,
            )
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            self.storage.delete(version.storage_key)
            raise ConflictError("版本号或内容冲突，请重试") from exc
        except Exception:
            self.db.rollback()
            self.storage.delete(version.storage_key)
            raise

        return document, version

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def list_documents(
        self, principal: Principal, query: DocumentQuery
    ) -> DocumentListResponse:
        owner_id = self._parse_uuid(query.owner_id, "owner_id")
        rows, total = self.documents.list_paginated(
            workspace_id=principal.user.workspace_id,
            keyword=query.keyword,
            status=query.status.value if query.status else None,
            tag=query.tag,
            sensitivity=query.sensitivity.value if query.sensitivity else None,
            owner_id=owner_id,
            created_from=query.created_from,
            created_to=query.created_to,
            include_deleted=query.include_deleted,
            sort_by=query.sort_by,
            order=query.order,
            page=query.page,
            page_size=query.page_size,
        )
        current_ids = [row.current_version_id for row in rows if row.current_version_id]
        versions = self.documents.get_versions(current_ids)
        return DocumentListResponse(
            items=[
                document_item(row, versions.get(row.current_version_id))
                for row in rows
            ],
            total=total,
            page=query.page,
            page_size=query.page_size,
        )

    def get_detail(self, principal: Principal, document_id: uuid.UUID) -> DocumentItem:
        document = self._get_or_404(document_id, principal.user.workspace_id)
        current = (
            self.documents.get_version(document.id, document.current_version_id)
            if document.current_version_id
            else None
        )
        return document_item(document, current)

    def list_versions(
        self, principal: Principal, document_id: uuid.UUID
    ) -> list[DocumentVersionItem]:
        document = self._get_or_404(document_id, principal.user.workspace_id)
        return [version_item(version) for version in self.documents.list_versions(document.id)]

    def build_download(
        self,
        principal: Principal,
        document_id: uuid.UUID,
        version_id: uuid.UUID,
        *,
        ip: str | None = None,
    ) -> DownloadUrl:
        document = self._get_or_404(document_id, principal.user.workspace_id)
        version = self.documents.get_version(document.id, version_id)
        if version is None:
            raise NotFoundError("文档版本不存在")

        expires = min(settings.minio_presign_expire_seconds, PRESIGN_MAX_SECONDS)
        url = self.storage.presigned_get(
            version.storage_key, expires_seconds=expires, file_name=version.file_name
        )
        self.audit.record(
            AuditAction.DOCUMENT_DOWNLOAD,
            actor=principal.user,
            object_type="document_version",
            object_id=version.id,
            after={
                "document_id": str(document.id),
                "version_no": version.version_no,
                "file_name": version.file_name,
            },
            ip=ip,
        )
        self.db.commit()
        return DownloadUrl(
            url=url,
            file_name=version.file_name,
            version_no=version.version_no,
            expires_in=expires,
        )

    # ------------------------------------------------------------------ #
    # 元数据维护
    # ------------------------------------------------------------------ #
    def update_document(
        self,
        principal: Principal,
        document_id: uuid.UUID,
        payload: DocumentUpdateRequest,
        *,
        ip: str | None = None,
    ) -> DocumentItem:
        document = self._get_or_404(document_id, principal.user.workspace_id)
        before = _snapshot(document)

        if payload.status is not None:
            if (
                document.status == DocumentStatus.PENDING.value
                and payload.status.value != DocumentStatus.PENDING.value
            ):
                # 审批中不允许绕过审批流直接改状态
                raise ConflictError(
                    "文档审批中，状态由审批结果决定，请先撤回审批",
                    data={"current": document.status, "target": payload.status.value},
                )
            state_machines.DOCUMENT.ensure(document.status, payload.status.value)
            document.status = payload.status.value
            if payload.status.value != DocumentStatus.PUBLISHED.value:
                # 离开已发布状态（如下架）后不应再被检索到
                self._reset_current_index(document)
        if payload.title is not None:
            document.title = payload.title
        if payload.summary is not None:
            document.summary = payload.summary
        if payload.tags is not None:
            document.tags = payload.tags
        if payload.extra_metadata is not None:
            document.extra_metadata = payload.extra_metadata
        if payload.sensitivity is not None:
            document.sensitivity = payload.sensitivity.value
        if payload.owner_id is not None:
            owner_id = self._parse_uuid(payload.owner_id, "owner_id")
            if owner_id is not None and self.db.get(AppUser, owner_id) is None:
                raise BadRequestError("负责人不存在")
            document.owner_id = owner_id

        self.audit.record(
            AuditAction.DOCUMENT_UPDATE,
            actor=principal.user,
            object_type="document",
            object_id=document.id,
            before=before,
            after=_snapshot(document),
            ip=ip,
        )
        self.db.commit()

        current = (
            self.documents.get_version(document.id, document.current_version_id)
            if document.current_version_id
            else None
        )
        return document_item(document, current)

    def soft_delete(
        self, principal: Principal, document_id: uuid.UUID, *, ip: str | None = None
    ) -> None:
        document = self._get_or_404(document_id, principal.user.workspace_id)
        if document.status == DocumentStatus.PENDING.value:
            raise ConflictError(
                "文档审批中，不能删除；请先撤回审批",
                data={"status": document.status},
            )
        document.deleted_at = datetime.now(UTC)
        self._reset_current_index(document)
        self.audit.record(
            AuditAction.DOCUMENT_DELETE,
            actor=principal.user,
            object_type="document",
            object_id=document.id,
            before={"deleted_at": None},
            after={"deleted_at": document.deleted_at.isoformat()},
            ip=ip,
        )
        self.db.commit()

    def restore(
        self, principal: Principal, document_id: uuid.UUID, *, ip: str | None = None
    ) -> DocumentItem:
        document = self._get_or_404(
            document_id, principal.user.workspace_id, include_deleted=True
        )
        if document.deleted_at is None:
            raise BadRequestError("文档未被删除，无需恢复")

        document.deleted_at = None
        self.audit.record(
            AuditAction.DOCUMENT_RESTORE,
            actor=principal.user,
            object_type="document",
            object_id=document.id,
            after={"status": document.status},
            ip=ip,
        )
        self.db.commit()
        current = (
            self.documents.get_version(document.id, document.current_version_id)
            if document.current_version_id
            else None
        )
        if (
            current is not None
            and document.status == DocumentStatus.PUBLISHED.value
            and document.deleted_at is None
        ):
            # 恢复后重新进入检索范围
            enqueue_version_index(current.id)
        return document_item(document, current)

    def _reset_current_index(self, document: Document) -> None:
        """当前版本离开可检索状态时清空片段并复位索引状态。"""
        if not document.current_version_id:
            return
        version = self.documents.get_version(document.id, document.current_version_id)
        if version is not None:
            reset_version_index(self.db, version)

    # ------------------------------------------------------------------ #
    # 内部
    # ------------------------------------------------------------------ #
    def _inspect(self, upload: UploadFile) -> tuple[FileProfile, int, str]:
        """扩展名 → 大小 → 文件头 → 哈希，顺序即校验顺序。"""
        profile = profile_of(upload.filename or "")
        stream = upload.file
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(0)
        ensure_size(size)

        head = stream.read(HEAD_BYTES)
        stream.seek(0)
        ensure_content_matches(profile.extension, head)

        checksum, real_size = stream_checksum(stream)
        ensure_size(real_size)
        return profile, real_size, checksum

    def _put_object(self, version: DocumentVersion, upload: UploadFile) -> None:
        self.storage.put_stream(
            version.storage_key,
            upload.file,
            version.file_size,
            content_type=version.mime_type,
            metadata={"checksum": version.checksum, "document-id": str(version.document_id)},
        )

    @staticmethod
    def _storage_key(
        document: Document, version: DocumentVersion, profile: FileProfile
    ) -> str:
        return (
            f"documents/{document.workspace_id}/{document.id}"
            f"/v{version.version_no}/{uuid.uuid4().hex}{profile.extension}"
        )

    @staticmethod
    def _stem(file_name: str) -> str:
        name = file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        stem = name.rsplit(".", 1)[0].strip()
        return stem or "未命名文档"

    @staticmethod
    def _parse_uuid(value: str | None, field_name: str) -> uuid.UUID | None:
        if not value:
            return None
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise BadRequestError(f"{field_name} 不是合法的 UUID") from exc

    def _get_or_404(
        self,
        document_id: uuid.UUID,
        workspace_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Document:
        document = self.documents.get(
            document_id, workspace_id=workspace_id, include_deleted=include_deleted
        )
        if document is None:
            raise NotFoundError("文档不存在")
        return document



