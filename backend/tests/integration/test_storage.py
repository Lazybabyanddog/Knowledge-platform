"""对象存储客户端测试（任务 3.2）：5MB 上传下载往返。"""

from __future__ import annotations

import io
import os
import uuid

import httpx
import pytest

from app.core.exceptions import NotFoundError
from app.infra.storage import get_storage


@pytest.fixture
def storage_key() -> str:
    return f"tests/{uuid.uuid4().hex}.bin"


def test_put_and_get_five_megabytes(storage_key: str) -> None:
    storage = get_storage()
    payload = os.urandom(5 * 1024 * 1024)

    storage.put_stream(
        storage_key,
        io.BytesIO(payload),
        len(payload),
        content_type="application/octet-stream",
        metadata={"checksum": "test"},
    )
    try:
        size, etag = storage.stat(storage_key)
        assert size == len(payload)
        assert etag
        assert storage.get_bytes(storage_key) == payload
    finally:
        storage.delete(storage_key)

    with pytest.raises(NotFoundError):
        storage.get_bytes(storage_key)


def test_presigned_url_serves_content(storage_key: str) -> None:
    storage = get_storage()
    payload = b"%PDF-1.7\npresigned\n%%EOF\n"
    storage.put_stream(
        storage_key, io.BytesIO(payload), len(payload), content_type="application/pdf"
    )
    try:
        url = storage.presigned_get(storage_key, expires_seconds=900, file_name="说明书.pdf")
        assert "X-Amz-Signature" in url or "X-Amz-Credential" in url.replace("%2F", "/")

        resp = httpx.get(url, timeout=30.0)
        assert resp.status_code == 200
        assert resp.content == payload
    finally:
        storage.delete(storage_key)
