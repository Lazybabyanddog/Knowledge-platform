"""文档管理集成测试（任务 3.4–3.9）。"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.infra.db.models import AuditLog, Document, DocumentVersion
from app.infra.storage import get_storage

BASE = "/api/v1/documents"
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"
DOCX = b"PK\x03\x04" + b"\x14\x00\x00\x00" + b"\x00" * 64


def _pdf(tag: bytes = b"1") -> bytes:
    return PDF + tag


def _upload(
    client: TestClient,
    headers: dict[str, str],
    *,
    name: str = "规格说明.pdf",
    content: bytes | None = None,
    mime: str = "application/octet-stream",
    **form: Any,
) -> httpx.Response:
    return client.post(
        BASE,
        headers=headers,
        files={"file": (name, content if content is not None else _pdf(), mime)},
        data={key: str(value) for key, value in form.items()},
    )


def _register(cleanup: list[uuid.UUID], payload: dict[str, Any]) -> uuid.UUID:
    document_id = uuid.UUID(payload["id"])
    cleanup.append(document_id)
    return document_id


def test_upload_creates_document_and_version(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    db: Session,
) -> None:
    resp = _upload(client, auth_headers, summary="一段摘要", tags='["制度","研发"]')

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    document_id = _register(cleanup_document, data)
    assert data["title"] == "规格说明"
    assert data["doc_type"] == "pdf"
    assert data["status"] == "draft"
    assert data["source_type"] == "upload"
    assert data["tags"] == ["制度", "研发"]
    assert data["summary"] == "一段摘要"
    assert data["current_version"]["version_no"] == 1
    assert data["current_version"]["is_current"] is True

    version = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document_id))
    assert version is not None
    assert version.checksum
    assert get_storage().get_bytes(version.storage_key).startswith(b"%PDF-")


def test_upload_persists_sensitivity_and_extra_metadata(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
) -> None:
    resp = _upload(
        client,
        auth_headers,
        title="合同扫描件",
        sensitivity="confidential",
        extra_metadata='{"合同编号": "HT-001", "页数": 3}',
    )

    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    _register(cleanup_document, data)
    assert data["title"] == "合同扫描件"
    assert data["sensitivity"] == "confidential"
    assert data["extra_metadata"] == {"合同编号": "HT-001", "页数": 3}


def test_upload_rejects_unsupported_extension(
    client: TestClient, auth_headers: dict[str, str], db: Session
) -> None:
    resp = _upload(client, auth_headers, name="病毒.exe", content=b"MZ\x90\x00")

    assert resp.status_code == 400
    assert "不支持的文件类型" in resp.json()["message"]
    assert db.scalar(select(Document).where(Document.title == "病毒")) is None


def test_upload_rejects_disguised_content(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = _upload(client, auth_headers, name="伪装.pdf", content=b"MZ\x90\x00payload")

    assert resp.status_code == 400
    assert "文件内容与类型不符" in resp.json()["message"]


def test_upload_rejects_oversize(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "upload_max_size_mb", 1)

    resp = _upload(client, auth_headers, content=_pdf(b"x" * (2 * 1024 * 1024)))

    assert resp.status_code == 413


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("说明.html", "<html><body>知识库</body></html>".encode()),
        ("扫描件.tiff", b"MM\x00*" + b"\x00" * 32),
    ],
)
def test_upload_accepts_html_and_tiff(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    name: str,
    content: bytes,
) -> None:
    resp = _upload(client, auth_headers, name=name, content=content)

    assert resp.status_code == 201, resp.text
    _register(cleanup_document, resp.json()["data"])


def test_batch_upload_reports_each_file(
    client: TestClient, auth_headers: dict[str, str], cleanup_document: list[uuid.UUID]
) -> None:
    files: list[tuple[str, tuple[str, bytes, str]]] = [
        ("files", (f"批量{i}.pdf", _pdf(bytes([64 + i])), "application/pdf")) for i in range(4)
    ]
    files.insert(2, ("files", ("批量二.exe", b"MZ\x90\x00", "application/octet-stream")))

    resp = client.post(BASE + "/batch", headers=auth_headers, files=files)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert (data["succeeded"], data["failed"]) == (4, 1)
    ok_items = [item for item in data["items"] if item["success"]]
    failed = next(item for item in data["items"] if not item["success"])
    assert len(ok_items) == 4
    assert all(item["document_id"] for item in ok_items)
    assert failed["file_name"] == "批量二.exe"
    assert failed["error"]["code"] > 0
    cleanup_document.extend(uuid.UUID(item["document_id"]) for item in ok_items)


def test_new_version_flow_and_duplicate_content(
    client: TestClient, auth_headers: dict[str, str], cleanup_document: list[uuid.UUID]
) -> None:
    created = _upload(client, auth_headers, name="手册.pdf", content=_pdf(b"v1"))
    document_id = _register(cleanup_document, created.json()["data"])
    v1_id = created.json()["data"]["current_version_id"]

    version = client.post(
        f"{BASE}/{document_id}/versions",
        headers=auth_headers,
        files={"file": ("手册.pdf", _pdf(b"v2"), "application/pdf")},
        data={"change_note": "修订第二章"},
    )

    assert version.status_code == 201, version.text
    v2 = version.json()["data"]
    assert v2["version_no"] == 2
    assert v2["is_current"] is False
    assert v2["change_note"] == "修订第二章"

    detail = client.get(f"{BASE}/{document_id}", headers=auth_headers).json()["data"]
    assert detail["current_version_id"] == v1_id

    versions = client.get(f"{BASE}/{document_id}/versions", headers=auth_headers).json()["data"]
    assert [item["version_no"] for item in versions] == [2, 1]

    duplicated = client.post(
        f"{BASE}/{document_id}/versions",
        headers=auth_headers,
        files={"file": ("手册.pdf", _pdf(b"v2"), "application/pdf")},
    )
    assert duplicated.status_code == 409

    # 相同内容落到另一篇文档不算重复
    other = _upload(client, auth_headers, name="副本.pdf", content=_pdf(b"v2"))
    assert other.status_code == 201, other.text
    _register(cleanup_document, other.json()["data"])


def test_download_returns_presigned_url_and_audits(
    client: TestClient, auth_headers: dict[str, str], cleanup_document: list[uuid.UUID], db: Session
) -> None:
    content = _pdf(b"download")
    created = _upload(client, auth_headers, name="下载.pdf", content=content)
    data = created.json()["data"]
    document_id = _register(cleanup_document, data)
    request_id = f"doc-download-{uuid.uuid4().hex[:8]}"

    resp = client.get(
        f"{BASE}/{document_id}/versions/{data['current_version_id']}/download",
        headers={**auth_headers, "X-Request-Id": request_id},
    )

    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]
    assert payload["expires_in"] == 900
    assert payload["file_name"] == "下载.pdf"
    assert "X-Amz-Expires=900" in payload["url"]

    download = httpx.get(payload["url"], timeout=30.0)
    assert download.status_code == 200
    assert download.content == content

    db.rollback()
    actions = db.scalars(
        select(AuditLog.action).where(AuditLog.request_id == request_id)
    ).all()
    assert "document.download" in actions


def test_version_unique_constraints(
    client: TestClient,
    auth_headers: dict[str, str],
    cleanup_document: list[uuid.UUID],
    db: Session,
) -> None:
    created = _upload(client, auth_headers)
    document_id = _register(cleanup_document, created.json()["data"])
    db.rollback()
    version = db.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id)
    )
    assert version is not None
    base = {
        "document_id": document_id,
        "storage_key": "documents/constraint-test",
        "file_name": "x.pdf",
        "file_size": 1,
        "mime_type": "application/pdf",
        "is_current": False,
    }

    db.add(DocumentVersion(version_no=1, checksum="sha-not-used", **base))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    db.add(DocumentVersion(version_no=2, checksum=version.checksum, **base))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_soft_delete_and_admin_restore(
    client: TestClient,
    auth_headers: dict[str, str],
    role_headers: Any,
    cleanup_document: list[uuid.UUID],
) -> None:
    created = _upload(client, auth_headers)
    document_id = _register(cleanup_document, created.json()["data"])

    deleted = client.delete(f"{BASE}/{document_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"{BASE}/{document_id}", headers=auth_headers).status_code == 404
    listed = client.get(BASE, headers=auth_headers, params={"keyword": "规格说明"}).json()["data"]
    assert all(item["id"] != str(document_id) for item in listed["items"])

    contributor = role_headers(["contributor"])
    assert client.post(f"{BASE}/{document_id}/restore", headers=contributor).status_code == 403

    restored = client.post(f"{BASE}/{document_id}/restore", headers=auth_headers)
    assert restored.status_code == 200, restored.text
    assert client.get(f"{BASE}/{document_id}", headers=auth_headers).status_code == 200


def test_status_transition_follows_state_machine(
    client: TestClient, auth_headers: dict[str, str], cleanup_document: list[uuid.UUID]
) -> None:
    created = _upload(client, auth_headers)
    document_id = _register(cleanup_document, created.json()["data"])

    illegal = client.patch(
        f"{BASE}/{document_id}", headers=auth_headers, json={"status": "published"}
    )
    assert illegal.status_code == 409

    pending = client.patch(
        f"{BASE}/{document_id}", headers=auth_headers, json={"status": "pending"}
    )
    assert pending.status_code == 200, pending.text

    # 审批中不允许通过 PATCH 绕过审批直接发布（发布只能由审批通过触发）
    bypass = client.patch(
        f"{BASE}/{document_id}", headers=auth_headers, json={"status": "published"}
    )
    assert bypass.status_code == 409
    assert "审批中" in bypass.json()["message"]


def test_list_filters_and_pagination(
    client: TestClient, auth_headers: dict[str, str], cleanup_document: list[uuid.UUID]
) -> None:
    first = _upload(
        client,
        auth_headers,
        name="甲.pdf",
        title="知识库制度",
        tags='["制度"]',
        sensitivity="internal",
    )
    second = _upload(
        client,
        auth_headers,
        name="乙.pdf",
        title="技术方案",
        tags='["研发"]',
        sensitivity="secret",
    )
    _register(cleanup_document, first.json()["data"])
    _register(cleanup_document, second.json()["data"])

    by_keyword = client.get(BASE, headers=auth_headers, params={"keyword": "制度"}).json()["data"]
    assert by_keyword["total"] == 1
    assert by_keyword["items"][0]["title"] == "知识库制度"

    by_tag = client.get(BASE, headers=auth_headers, params={"tag": "研发"}).json()["data"]
    assert [item["title"] for item in by_tag["items"]] == ["技术方案"]

    by_sensitivity = client.get(
        BASE, headers=auth_headers, params={"sensitivity": "secret"}
    ).json()["data"]
    assert by_sensitivity["total"] == 1

    paged = client.get(
        BASE, headers=auth_headers, params={"page": 1, "page_size": 1}
    ).json()["data"]
    assert paged["total"] >= 2
    assert len(paged["items"]) == 1

    empty = client.get(BASE, headers=auth_headers, params={"keyword": "不存在的标题"})
    assert empty.status_code == 200
    assert empty.json()["data"] == {"items": [], "total": 0, "page": 1, "page_size": 20}


def test_extra_metadata_key_limit(
    client: TestClient, auth_headers: dict[str, str], cleanup_document: list[uuid.UUID]
) -> None:
    created = _upload(client, auth_headers)
    document_id = _register(cleanup_document, created.json()["data"])

    resp = client.patch(
        f"{BASE}/{document_id}",
        headers=auth_headers,
        json={"extra_metadata": {f"k{i}": i for i in range(51)}},
    )

    assert resp.status_code == 400
