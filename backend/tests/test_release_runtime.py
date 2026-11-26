"""发布相关的运行时行为：就绪探针分档、存活探针的版本标识、beat 单实例锁。

这些用例不依赖真实中间件：探针被替换成可编排的假实现，锁用内存假 Redis。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import readiness
from app.core.config import settings
from app.main import create_app
from app.workers import beat_launcher


def _ok() -> None:
    return None


def _boom(message: str = "依赖不可用"):
    def _raise() -> None:
        raise RuntimeError(message)

    return _raise


@pytest.fixture(autouse=True)
def _reset_readiness() -> None:
    readiness.reset_readiness_cache()
    yield
    readiness.reset_readiness_cache()


def _use_dependencies(monkeypatch: pytest.MonkeyPatch, *deps: readiness.Dependency) -> None:
    monkeypatch.setattr(readiness, "DEPENDENCIES", deps)


def test_readyz_ok_when_all_dependencies_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_dependencies(
        monkeypatch,
        readiness.Dependency("postgres", readiness.REQUIRED, _ok),
        readiness.Dependency("neo4j", readiness.DEGRADABLE, _ok),
    )

    report = readiness.check_readiness(use_cache=False)

    assert report["status"] == "ok"
    assert report["failed_required"] == []
    assert report["degraded"] == []


def test_readyz_unavailable_when_required_dependency_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_dependencies(
        monkeypatch,
        readiness.Dependency("postgres", readiness.REQUIRED, _boom()),
        readiness.Dependency("neo4j", readiness.DEGRADABLE, _ok),
    )

    report = readiness.check_readiness(use_cache=False)

    assert report["status"] == "unavailable"
    assert report["failed_required"] == ["postgres"]
    assert report["dependencies"]["postgres"]["detail"] == "依赖不可用"


def test_readyz_degraded_when_only_optional_dependency_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_dependencies(
        monkeypatch,
        readiness.Dependency("postgres", readiness.REQUIRED, _ok),
        readiness.Dependency("neo4j", readiness.DEGRADABLE, _boom("图库断开")),
    )

    report = readiness.check_readiness(use_cache=False)

    # 可降级依赖不可用时仍允许接流量，只是把降级项列出来
    assert report["status"] == "degraded"
    assert report["degraded"] == ["neo4j"]
    assert report["failed_required"] == []


def test_readyz_endpoint_returns_503_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.main.check_readiness",
        lambda **_: {"status": "unavailable", "failed_required": ["postgres"]},
    )
    client = TestClient(create_app(), raise_server_exceptions=False)

    resp = client.get("/readyz")

    assert resp.status_code == 503
    assert resp.json()["data"]["failed_required"] == ["postgres"]


def test_readyz_endpoint_returns_200_when_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.main.check_readiness",
        lambda **_: {"status": "degraded", "degraded": ["neo4j"]},
    )
    client = TestClient(create_app(), raise_server_exceptions=False)

    resp = client.get("/readyz")

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "degraded"


def test_healthz_exposes_release_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "release_version", "v20261125-1")
    monkeypatch.setattr(settings, "release_color", "green")
    client = TestClient(create_app(), raise_server_exceptions=False)

    body = client.get("/healthz").json()["data"]

    assert body["release"] == "v20261125-1"
    assert body["color"] == "green"
    # 框架版本仍保留，便于和发布版本区分
    assert body["version"]


class FakeRedis:
    """只实现 beat 锁用到的三个动作。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.ttl_ms: dict[str, int] = {}

    def set(self, key: str, value: str, nx: bool = False, px: int | None = None) -> Any:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if px is not None:
            self.ttl_ms[key] = px
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:  # noqa: ARG002
        if self.store.get(key) != args[0]:
            return 0
        if "del" in script:
            self.store.pop(key, None)
            self.ttl_ms.pop(key, None)
            return 1
        self.ttl_ms[key] = int(args[1])
        return 1


def test_beat_lock_allows_only_one_holder() -> None:
    redis = FakeRedis()

    assert beat_launcher.acquire(redis, "blue-1", 30_000) is True
    assert beat_launcher.acquire(redis, "green-1", 30_000) is False
    assert redis.get(beat_launcher.LOCK_KEY) == "blue-1"


def test_beat_lock_renew_requires_owner() -> None:
    redis = FakeRedis()
    beat_launcher.acquire(redis, "blue-1", 30_000)

    assert beat_launcher.renew(redis, "blue-1", 45_000) is True
    assert redis.ttl_ms[beat_launcher.LOCK_KEY] == 45_000
    # 非持有者不能续租，否则锁过期后会被旧的持有者"抢回去"
    assert beat_launcher.renew(redis, "green-1", 45_000) is False


def test_beat_lock_can_be_taken_after_release() -> None:
    redis = FakeRedis()
    beat_launcher.acquire(redis, "blue-1", 30_000)

    beat_launcher.release(redis, "blue-1")

    assert beat_launcher.acquire(redis, "green-1", 30_000) is True


def test_check_env_fails_when_no_engine_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scripts import check_env

    monkeypatch.setattr("app.parsing.engine_names", lambda: [])

    assert check_env.main() == 1
