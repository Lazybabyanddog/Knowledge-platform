"""密码哈希与令牌签发校验。

两个约定：
1. 访问令牌是 JWT，自包含角色与空间，接口鉴权不再查库；
2. 刷新令牌是随机串，只把 SHA-256 哈希落库，支持吊销与重放检测。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings
from app.core.exceptions import TokenExpiredError, UnauthorizedError

# bcrypt 只取前 72 字节，超长直接截断，避免库抛异常
_BCRYPT_MAX_BYTES = 72


def _encode_password(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_encode_password(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_encode_password(password), password_hash.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(
    *,
    subject: str,
    username: str,
    roles: list[str],
    workspace_id: str,
) -> tuple[str, datetime]:
    """返回 (token, expires_at)。"""
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=settings.access_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": subject,
        "username": username,
        "roles": roles,
        "workspace_id": workspace_id,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_at


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("令牌无效") from exc

    if payload.get("type") != "access":
        raise UnauthorizedError("令牌类型不匹配")
    return payload


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """返回 (明文令牌, 落库哈希)。明文只在响应中出现一次。"""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)
