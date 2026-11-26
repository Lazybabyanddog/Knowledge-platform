"""就绪检查：只回答"这个实例现在能不能接流量"。

与存活检查分开的原因：liveness 一旦检查外部依赖，数据库抖动就会让容器被反复重启，
把小故障放大成不可用；readiness 只用于"要不要把流量切过来"。

依赖分两类：
- 必需：缺了主链路就不能服务（Postgres / Redis / 对象存储）；
- 可降级：缺了只影响部分功能（Neo4j），报告 degraded 但仍可接流量。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

# 单项检查上限：探针必须比"用户能忍受的等待"短，否则切流脚本的轮询会排队
CHECK_TIMEOUT_SECONDS = 1.5
# 结果缓存：发布脚本每 2s 轮询一次，没有缓存等于每 2s 打一轮全量依赖
CACHE_TTL_SECONDS = 3.0

REQUIRED = "required"
DEGRADABLE = "degradable"


@dataclass(frozen=True)
class Dependency:
    name: str
    kind: str
    probe: Callable[[], None]


def _check_postgres() -> None:
    from sqlalchemy import text

    from app.infra.db.session import SessionLocal

    with SessionLocal() as session:
        session.execute(text("SELECT 1"))


def _check_redis() -> None:
    from app.infra.cache.redis_client import get_redis

    get_redis().ping()


def _check_storage() -> None:
    from app.infra.storage.s3_client import get_storage

    get_storage().ping()


def _check_neo4j() -> None:
    from app.infra.graph.driver import check_connectivity

    if not check_connectivity():
        raise RuntimeError("Neo4j 连接失败")


DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency("postgres", REQUIRED, _check_postgres),
    Dependency("redis", REQUIRED, _check_redis),
    Dependency("storage", REQUIRED, _check_storage),
    Dependency("neo4j", DEGRADABLE, _check_neo4j),
)

_pool = ThreadPoolExecutor(max_workers=len(DEPENDENCIES), thread_name_prefix="readyz")
_cache: tuple[float, dict[str, object]] | None = None
# 每个依赖最多一个在飞：依赖卡住时不能让探针把线程池灌满
_pending: dict[str, Future[None]] = {}


def _probe(dependency: Dependency) -> dict[str, str]:
    running = _pending.get(dependency.name)
    if running is not None and not running.done():
        return {"status": "timeout", "detail": "上一次检查仍未返回"}

    future = _pool.submit(dependency.probe)
    _pending[dependency.name] = future
    try:
        future.result(timeout=CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        # 超时的线程可能仍在跑，取消只是标记；探针本身要自带 socket 超时
        future.cancel()
        return {"status": "timeout", "detail": f"{CHECK_TIMEOUT_SECONDS}s 内未返回"}
    except Exception as exc:  # noqa: BLE001 - 探针失败原因需要原样上报
        return {"status": "error", "detail": str(exc) or exc.__class__.__name__}
    return {"status": "ok"}


def check_readiness(*, use_cache: bool = True) -> dict[str, object]:
    """返回可直接序列化的就绪报告。"""
    global _cache

    now = time.monotonic()
    if use_cache and _cache is not None and now - _cache[0] < CACHE_TTL_SECONDS:
        return _cache[1]

    results = {dependency.name: _probe(dependency) for dependency in DEPENDENCIES}
    failed_required = [
        dependency.name
        for dependency in DEPENDENCIES
        if dependency.kind == REQUIRED and results[dependency.name]["status"] != "ok"
    ]
    degraded = [
        dependency.name
        for dependency in DEPENDENCIES
        if dependency.kind == DEGRADABLE and results[dependency.name]["status"] != "ok"
    ]

    if failed_required:
        status = "unavailable"
    elif degraded:
        status = "degraded"
    else:
        status = "ok"

    report: dict[str, object] = {
        "status": status,
        "version": settings.release_version,
        "color": settings.release_color,
        "dependencies": results,
        "failed_required": failed_required,
        "degraded": degraded,
    }
    _cache = (now, report)
    return report


def reset_readiness_cache() -> None:
    """测试用：清掉缓存，避免上一个用例的结论影响下一个。"""
    global _cache
    _cache = None
    _pending.clear()
