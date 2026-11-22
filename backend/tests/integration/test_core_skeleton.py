"""验证统一响应体、异常处理器与 request_id 中间件（任务 1.2）。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.exceptions import BadRequestError, NotFoundError
from app.main import create_app


def build_client() -> TestClient:
    app = create_app()
    probe = APIRouter()

    @probe.get("/probe/plain")
    async def _plain() -> dict[str, str]:
        return {"hello": "world"}

    @probe.get("/probe/business-error")
    async def _business_error() -> None:
        raise BadRequestError("字段非法")

    @probe.get("/probe/not-found")
    async def _not_found() -> None:
        raise NotFoundError()

    @probe.post("/probe/validate")
    async def _validate(payload: dict[str, int]) -> dict[str, int]:  # noqa: ARG001
        return {"ok": 1}

    app.include_router(probe, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def test_healthz_returns_envelope() -> None:
    client = build_client()
    resp = client.get("/healthz")

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "ok"
    assert body["data"]["status"] == "ok"
    assert body["request_id"]


def test_request_id_header_is_echoed_and_injected() -> None:
    client = build_client()
    resp = client.get("/healthz", headers={"X-Request-Id": "trace-0001"})

    assert resp.headers["X-Request-Id"] == "trace-0001"
    assert resp.json()["request_id"] == "trace-0001"


def test_plain_api_payload_is_wrapped() -> None:
    client = build_client()
    body = client.get("/api/v1/probe/plain").json()

    assert body["code"] == 0
    assert body["data"] == {"hello": "world"}


def test_business_error_uses_mapped_code() -> None:
    client = build_client()
    resp = client.get("/api/v1/probe/business-error")

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 40001
    assert body["message"] == "字段非法"


def test_not_found_error() -> None:
    client = build_client()
    resp = client.get("/api/v1/probe/not-found")

    assert resp.status_code == 404
    assert resp.json()["code"] == 40401


def test_validation_error_is_normalized() -> None:
    client = build_client()
    resp = client.post("/api/v1/probe/validate", json={"wrong": "type"})

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 40001
    assert body["data"]
