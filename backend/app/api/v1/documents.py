"""文档管理接口：上传、版本、下载与元数据维护。"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.deps import Principal, client_ip, require_permission, require_roles
from app.core.exceptions import AppError, BadRequestError
from app.domain.enums import RoleCode, Sensitivity
from app.domain.policies import Permission
from app.infra.db.session import get_db
from app.schemas.document import (
    BatchUploadItem,
    BatchUploadResponse,
    DocumentItem,
    DocumentListResponse,
    DocumentQuery,
    DocumentUpdateRequest,
    DocumentVersionItem,
    DownloadUrl,
)
from app.services.document_service import DocumentMeta, DocumentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["文档管理"])

DbSession = Annotated[Session, Depends(get_db)]
ReadPrincipal = Annotated[Principal, Depends(require_permission(Permission.DOCUMENT_READ))]
WritePrincipal = Annotated[Principal, Depends(require_permission(Permission.DOCUMENT_WRITE))]
DeletePrincipal = Annotated[Principal, Depends(require_permission(Permission.DOCUMENT_DELETE))]
AdminPrincipal = Annotated[Principal, Depends(require_roles(RoleCode.ADMIN.value))]


def _parse_tags(raw: str | None) -> list[str]:
    """同时接受 JSON 数组与逗号分隔，前端两种写法都常见。"""
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BadRequestError("tags 不是合法的 JSON 数组") from exc
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise BadRequestError("tags 必须是字符串数组")
        items = value
    else:
        items = text.split(",")
    cleaned = list(dict.fromkeys(item.strip() for item in items if item.strip()))
    if len(cleaned) > 50:
        raise BadRequestError("标签数量不得超过 50 个")
    if any(len(item) > 32 for item in cleaned):
        raise BadRequestError("单个标签长度不得超过 32 个字符")
    return cleaned


def _parse_metadata(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadRequestError("extra_metadata 不是合法的 JSON") from exc
    if not isinstance(value, dict):
        raise BadRequestError("extra_metadata 必须是对象")
    if len(value) > 50:
        raise BadRequestError("扩展元数据最多 50 个键")
    return value


def _parse_sensitivity(raw: str | None) -> Sensitivity:
    if not raw:
        return Sensitivity.INTERNAL
    try:
        return Sensitivity(raw)
    except ValueError as exc:
        raise BadRequestError(f"未知密级: {raw}") from exc


@router.post("", response_model=DocumentItem, status_code=201, summary="上传文档")
def upload_document(
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
    file: Annotated[UploadFile, File(description="待上传文件")],
    title: Annotated[str | None, Form()] = None,
    summary: Annotated[str | None, Form()] = None,
    sensitivity: Annotated[str | None, Form()] = None,
    tags: Annotated[str | None, Form()] = None,
    extra_metadata: Annotated[str | None, Form()] = None,
) -> DocumentItem:
    meta = DocumentMeta(
        title=title,
        summary=summary,
        sensitivity=_parse_sensitivity(sensitivity),
        tags=_parse_tags(tags),
        extra_metadata=_parse_metadata(extra_metadata),
    )
    service = DocumentService(db)
    document, version = service.create_document(
        principal, file, meta, ip=client_ip(request)
    )
    return service.attributed_item(document, version)


@router.post("/batch", response_model=BatchUploadResponse, summary="批量上传")
def upload_batch(
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
    files: Annotated[list[UploadFile], File(description="多个文件")],
    sensitivity: Annotated[str | None, Form()] = None,
    tags: Annotated[str | None, Form()] = None,
) -> BatchUploadResponse:
    """逐个独立处理：单个文件失败只影响自身，返回逐项结果。"""
    service = DocumentService(db)
    meta_args = {
        "sensitivity": _parse_sensitivity(sensitivity),
        "tags": _parse_tags(tags),
    }
    items: list[BatchUploadItem] = []
    for upload in files:
        try:
            document, version = service.create_document(
                principal,
                upload,
                DocumentMeta(**meta_args),
                ip=client_ip(request),
            )
            items.append(
                BatchUploadItem(
                    file_name=upload.filename or "",
                    success=True,
                    document_id=str(document.id),
                    version_id=str(version.id),
                )
            )
        except AppError as exc:
            db.rollback()
            items.append(
                BatchUploadItem(
                    file_name=upload.filename or "",
                    success=False,
                    error={"code": int(exc.code), "message": exc.message},
                )
            )
        except Exception:  # pragma: no cover - 兜底，保证整批不中断
            db.rollback()
            logger.exception("批量上传单文件失败 file=%s", upload.filename)
            items.append(
                BatchUploadItem(
                    file_name=upload.filename or "",
                    success=False,
                    error={"code": 50000, "message": "文件处理失败"},
                )
            )

    succeeded = sum(1 for item in items if item.success)
    return BatchUploadResponse(
        items=items, succeeded=succeeded, failed=len(items) - succeeded
    )


@router.post(
    "/{document_id}/versions",
    response_model=DocumentVersionItem,
    status_code=201,
    summary="上传新版本",
)
def upload_version(
    document_id: uuid.UUID,
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
    file: Annotated[UploadFile, File(description="新版本文件")],
    change_note: Annotated[str | None, Form()] = None,
) -> DocumentVersionItem:
    service = DocumentService(db)
    _, version = service.add_version(
        principal,
        document_id,
        file,
        change_note=change_note,
        ip=client_ip(request),
    )
    return service.version_item_with_uploader(version)


@router.get(
    "/{document_id}/versions",
    response_model=list[DocumentVersionItem],
    summary="版本列表",
)
def list_versions(
    document_id: uuid.UUID, principal: ReadPrincipal, db: DbSession
) -> list[DocumentVersionItem]:
    return DocumentService(db).list_versions(principal, document_id)


@router.get(
    "/{document_id}/versions/{version_id}/download",
    response_model=DownloadUrl,
    summary="获取下载地址",
)
def download_version(
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    request: Request,
    principal: ReadPrincipal,
    db: DbSession,
) -> DownloadUrl:
    return DocumentService(db).build_download(
        principal, document_id, version_id, ip=client_ip(request)
    )


@router.get("", response_model=DocumentListResponse, summary="文档列表")
def list_documents(
    query: Annotated[DocumentQuery, Query()],
    principal: ReadPrincipal,
    db: DbSession,
) -> DocumentListResponse:
    return DocumentService(db).list_documents(principal, query)


@router.get("/{document_id}", response_model=DocumentItem, summary="文档详情")
def get_document(
    document_id: uuid.UUID, principal: ReadPrincipal, db: DbSession
) -> DocumentItem:
    return DocumentService(db).get_detail(principal, document_id)


@router.patch("/{document_id}", response_model=DocumentItem, summary="修改文档")
def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdateRequest,
    request: Request,
    principal: WritePrincipal,
    db: DbSession,
) -> DocumentItem:
    return DocumentService(db).update_document(
        principal, document_id, payload, ip=client_ip(request)
    )


@router.delete("/{document_id}", summary="软删除文档")
def delete_document(
    document_id: uuid.UUID,
    request: Request,
    principal: DeletePrincipal,
    db: DbSession,
) -> dict[str, bool]:
    DocumentService(db).soft_delete(principal, document_id, ip=client_ip(request))
    return {"deleted": True}


@router.post("/{document_id}/restore", response_model=DocumentItem, summary="恢复文档")
def restore_document(
    document_id: uuid.UUID,
    request: Request,
    principal: AdminPrincipal,
    db: DbSession,
) -> DocumentItem:
    return DocumentService(db).restore(principal, document_id, ip=client_ip(request))
