"""对象存储：S3 兼容客户端（本地用 pgsty/silo 提供 MinIO 协议）。

业务只依赖 ``ObjectStorage`` 的四个动作：流式上传、预签名下载、读取、删除。
预签名把读取权限收束为"短时 URL"，避免把凭据下发给前端。
"""

from __future__ import annotations

import io
import logging
from datetime import timedelta
from functools import lru_cache
from typing import BinaryIO
from urllib.parse import quote

from minio import Minio
from minio.error import S3Error

from app.core.config import settings
from app.core.exceptions import InternalError, NotFoundError

logger = logging.getLogger(__name__)


class ObjectStorage:
    def __init__(
        self,
        *,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        secure: bool | None = None,
    ) -> None:
        self.bucket = bucket or settings.minio_bucket
        self._client = Minio(
            endpoint or settings.minio_endpoint,
            access_key=access_key or settings.minio_access_key,
            secret_key=secret_key or settings.minio_secret_key,
            secure=settings.minio_secure if secure is None else secure,
        )
        self._bucket_ready = False

    # ---- 基础设施 ----
    def ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
        except S3Error as exc:  # pragma: no cover - 依赖外部服务
            raise InternalError(f"对象存储不可用: {exc.code}") from exc
        self._bucket_ready = True

    def ping(self) -> None:
        """就绪检查用：只确认 bucket 可读，不创建（创建是写操作，探针不该有副作用）。"""
        self._client.bucket_exists(self.bucket)

    # ---- 写入 ----
    def put_stream(
        self,
        key: str,
        stream: BinaryIO,
        length: int,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> None:
        """流式写入：调用方给出长度，SDK 分片读取，不在内存里聚合整个文件。"""
        self.ensure_bucket()
        stream.seek(0)
        try:
            self._client.put_object(
                self.bucket,
                key,
                stream,
                length,
                content_type=content_type,
                metadata=metadata or {},
            )
        except S3Error as exc:  # pragma: no cover - 依赖外部服务
            raise InternalError(f"对象写入失败: {exc.code}") from exc

    # ---- 读取 ----
    def presigned_get(
        self,
        key: str,
        *,
        expires_seconds: int | None = None,
        file_name: str | None = None,
    ) -> str:
        self.ensure_bucket()
        expires = expires_seconds or settings.minio_presign_expire_seconds
        headers: dict[str, str] = {}
        if file_name:
            headers["response-content-disposition"] = (
                f"attachment; filename*=UTF-8''{quote(file_name)}"
            )
        try:
            return self._client.presigned_get_object(
                self.bucket, key, expires=timedelta(seconds=expires), response_headers=headers
            )
        except S3Error as exc:  # pragma: no cover - 依赖外部服务
            raise InternalError(f"下载地址生成失败: {exc.code}") from exc

    def get_bytes(self, key: str) -> bytes:
        self.ensure_bucket()
        try:
            response = self._client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                raise NotFoundError("对象不存在") from exc
            raise InternalError(f"对象读取失败: {exc.code}") from exc

    def stat(self, key: str) -> tuple[int, str]:
        """返回 (size, etag)，供一致性校验使用。"""
        self.ensure_bucket()
        try:
            info = self._client.stat_object(self.bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                raise NotFoundError("对象不存在") from exc
            raise InternalError(f"对象信息读取失败: {exc.code}") from exc
        return info.size or 0, (info.etag or "").strip('"')

    # ---- 删除 ----
    def delete(self, key: str) -> None:
        self.ensure_bucket()
        try:
            self._client.remove_object(self.bucket, key)
        except S3Error as exc:  # pragma: no cover - 依赖外部服务
            logger.warning("对象删除失败 key=%s code=%s", key, exc.code)


@lru_cache
def get_storage() -> ObjectStorage:
    return ObjectStorage()


def reset_storage_cache() -> None:
    """测试替换配置后清空单例。"""
    get_storage.cache_clear()


def stream_checksum(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
    """边读边算 SHA-256，返回 (hexdigest, size)，读完把指针复位到开头。"""
    import hashlib

    digest = hashlib.sha256()
    size = 0
    stream.seek(0)
    while chunk := stream.read(chunk_size):
        digest.update(chunk)
        size += len(chunk)
    stream.seek(0)
    return digest.hexdigest(), size


def bytes_stream(data: bytes) -> BinaryIO:
    """测试/小文件场景的便捷包装。"""
    return io.BytesIO(data)
