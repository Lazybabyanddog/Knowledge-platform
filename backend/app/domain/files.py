"""上传文件的类型判定与内容校验。

两道闸门，顺序固定：
1. 扩展名白名单（配置项 ``upload_allowed_extensions``）；
2. 文件头签名与扩展名匹配，拦截"改名伪装"的文件。
文本类格式没有魔数，改用"NUL 字节出现即视为二进制"的保守判定。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.exceptions import BadRequestError, PayloadTooLargeError

# 扩展名 -> (doc_type, mime_type)
_FILE_TYPES: dict[str, tuple[str, str]] = {
    ".pdf": ("pdf", "application/pdf"),
    ".doc": ("doc", "application/msword"),
    ".docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".ppt": ("ppt", "application/vnd.ms-powerpoint"),
    ".pptx": (
        "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ),
    ".xls": ("xls", "application/vnd.ms-excel"),
    ".xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".txt": ("txt", "text/plain"),
    ".md": ("md", "text/markdown"),
    ".html": ("html", "text/html"),
    ".csv": ("csv", "text/csv"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".tiff": ("image", "image/tiff"),
}

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

# 只声明"有魔数"的格式；未列出的按纯文本处理
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".doc": (_OLE_MAGIC,),
    ".ppt": (_OLE_MAGIC,),
    ".xls": (_OLE_MAGIC,),
    ".docx": _ZIP_MAGIC,
    ".pptx": _ZIP_MAGIC,
    ".xlsx": _ZIP_MAGIC,
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".tiff": (b"II*\x00", b"MM\x00*"),
}

HEAD_BYTES = 16


@dataclass(frozen=True, slots=True)
class FileProfile:
    extension: str
    doc_type: str
    mime_type: str
    file_name: str


def _allowed() -> frozenset[str]:
    return frozenset(ext.lower() for ext in settings.upload_allowed_extensions)


def parse_extension(file_name: str) -> str:
    name = (file_name or "").strip()
    if not name or "." not in name.rsplit("/", 1)[-1]:
        raise BadRequestError("文件缺少扩展名，无法识别类型")
    extension = "." + name.rsplit(".", 1)[1].lower()
    if extension not in _allowed():
        raise BadRequestError(
            f"不支持的文件类型: {extension}",
            data={"allowed_extensions": sorted(_allowed())},
        )
    return extension


def profile_of(file_name: str) -> FileProfile:
    extension = parse_extension(file_name)
    doc_type, mime_type = _FILE_TYPES[extension]
    return FileProfile(
        extension=extension,
        doc_type=doc_type,
        mime_type=mime_type,
        file_name=file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
    )


def ensure_size(size: int) -> None:
    if size <= 0:
        raise BadRequestError("文件内容为空")
    limit = settings.upload_max_size_bytes
    if size > limit:
        raise PayloadTooLargeError(
            f"单个文件不得超过 {settings.upload_max_size_mb} MB",
            data={"max_size_bytes": limit, "size": size},
        )


def ensure_content_matches(extension: str, head: bytes) -> None:
    """文件头校验：有魔数的格式必须匹配，文本格式不允许出现 NUL。"""
    signatures = _SIGNATURES.get(extension)
    if signatures is None:
        if b"\x00" in head:
            raise BadRequestError("文件内容与类型不符")
        return
    if not any(head.startswith(signature) for signature in signatures):
        raise BadRequestError("文件内容与类型不符")
