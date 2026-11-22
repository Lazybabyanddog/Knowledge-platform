"""密码哈希与 JWT 的单元测试（任务 2.3）。"""

from __future__ import annotations

import jwt
import pytest

from app.core.config import settings
from app.core.exceptions import TokenExpiredError, UnauthorizedError
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)


def test_hash_is_not_reversible_and_verified() -> None:
    raw = "Admin@12345"
    hashed = hash_password(raw)

    assert hashed != raw
    assert raw not in hashed
    assert verify_password(raw, hashed)
    assert not verify_password("wrong-password", hashed)


def test_same_password_gets_different_salt() -> None:
    assert hash_password("same") != hash_password("same")


def test_long_password_is_truncated_not_rejected() -> None:
    long_password = "a" * 200
    hashed = hash_password(long_password)

    assert verify_password(long_password, hashed)


def test_invalid_hash_does_not_raise() -> None:
    assert not verify_password("any", "not-a-bcrypt-hash")


def test_access_token_roundtrip() -> None:
    token, expires_at = create_access_token(
        subject="11111111-1111-1111-1111-111111111111",
        username="admin",
        roles=["admin"],
        workspace_id="22222222-2222-2222-2222-222222222222",
    )
    payload = decode_access_token(token)

    assert payload["sub"] == "11111111-1111-1111-1111-111111111111"
    assert payload["username"] == "admin"
    assert payload["roles"] == ["admin"]
    assert payload["type"] == "access"
    assert expires_at.timestamp() == pytest.approx(payload["exp"], abs=1)


def test_expired_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "access_token_ttl_seconds", -10)
    token, _ = create_access_token(
        subject="u1", username="admin", roles=["admin"], workspace_id="w1"
    )

    with pytest.raises(TokenExpiredError):
        decode_access_token(token)


def test_tampered_token_raises() -> None:
    token, _ = create_access_token(
        subject="u1", username="admin", roles=["admin"], workspace_id="w1"
    )
    parts = token.split(".")
    tampered = ".".join([parts[0], parts[1], "signature"])

    with pytest.raises(UnauthorizedError):
        decode_access_token(tampered)


def test_wrong_token_type_raises() -> None:
    forged = jwt.encode(
        {"sub": "u1", "type": "refresh", "exp": 9999999999},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    with pytest.raises(UnauthorizedError):
        decode_access_token(forged)


def test_refresh_token_is_hashed() -> None:
    raw, digest = new_refresh_token()

    assert raw != digest
    assert len(digest) == 64
    assert hash_refresh_token(raw) == digest
    assert new_refresh_token()[0] != raw
