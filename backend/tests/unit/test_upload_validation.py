"""文件类型与内容校验的单元测试（任务 3.3）。"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.core.exceptions import BadRequestError, PayloadTooLargeError
from app.domain.files import (
    HEAD_BYTES,
    ensure_content_matches,
    ensure_size,
    profile_of,
)

PDF_HEAD = b"%PDF-1.7\n"
OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_HEAD = b"PK\x03\x04"
PNG_HEAD = b"\x89PNG\r\n\x1a\n"
JPEG_HEAD = b"\xff\xd8\xff\xe0"


@pytest.mark.parametrize(
    ("name", "doc_type", "mime"),
    [
        ("a.pdf", "pdf", "application/pdf"),
        (
            "a.DOCX",
            "docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (
            "a.pptx",
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ("a.xlsx", "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("a.html", "html", "text/html"),
        ("a.tiff", "image", "image/tiff"),
        ("a.jpeg", "image", "image/jpeg"),
    ],
)
def test_profile_maps_extension(name: str, doc_type: str, mime: str) -> None:
    profile = profile_of(name)

    assert profile.doc_type == doc_type
    assert profile.mime_type == mime


@pytest.mark.parametrize("name", ["virus.exe", "noext", "archive.zip", "shell.sh"])
def test_unsupported_extension_rejected(name: str) -> None:
    with pytest.raises(BadRequestError):
        profile_of(name)


@pytest.mark.parametrize(
    ("name", "head"),
    [
        ("a.pdf", PDF_HEAD),
        ("a.docx", ZIP_HEAD + b"\x00" * 8),
        ("a.doc", OLE_HEAD + b"\x00" * 8),
        ("a.png", PNG_HEAD + b"\x00" * 8),
        ("a.jpg", JPEG_HEAD + b"\x00" * 8),
        ("a.tiff", b"II*\x00" + b"\x00" * 8),
    ],
)
def test_signature_match(name: str, head: bytes) -> None:
    ensure_content_matches(profile_of(name).extension, head)


@pytest.mark.parametrize(
    ("name", "head"),
    [
        ("伪装.pdf", b"MZ\x90\x00"),  # 可执行文件改后缀
        ("伪装.docx", PDF_HEAD),  # PDF 改后缀
        ("伪装.png", JPEG_HEAD),
    ],
)
def test_signature_mismatch_rejected(name: str, head: bytes) -> None:
    with pytest.raises(BadRequestError):
        ensure_content_matches(profile_of(name).extension, head)


def test_text_file_rejects_binary_content() -> None:
    with pytest.raises(BadRequestError):
        ensure_content_matches(".txt", b"hello\x00world")

    ensure_content_matches(".md", "# 标题\n正文".encode())


def test_empty_file_rejected() -> None:
    with pytest.raises(BadRequestError):
        ensure_size(0)


def test_oversize_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "upload_max_size_mb", 1)

    with pytest.raises(PayloadTooLargeError) as exc_info:
        ensure_size(2 * 1024 * 1024)

    assert exc_info.value.code is not None
    ensure_size(1024 * 1024)


def test_head_bytes_is_enough_for_all_signatures() -> None:
    assert HEAD_BYTES >= 8
