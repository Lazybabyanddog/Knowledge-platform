"""FastAPI 应用装配：中间件、异常处理器、路由挂载。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.readiness import check_readiness
from app.core.response import EnvelopeMiddleware, ok


def create_app() -> FastAPI:
    configure_logging(logging.DEBUG if settings.debug else logging.INFO)
    _verify_parsing_engines()

    app = FastAPI(
        title="AI Platform API",
        description="企业知识库平台：文档解析、知识图谱、审批治理",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    # 添加顺序即内到外：最后添加的最外层
    app.add_middleware(EnvelopeMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    register_exception_handlers(app)
    _register_routers(app)
    _log_recovery_config()

    @app.get("/healthz", tags=["system"], summary="存活检查")
    async def healthz() -> object:
        # 只反映进程存活：查依赖会因抖动触发容器反复重启，那是 /readyz 的职责
        return ok(
            {
                "status": "ok",
                "env": settings.app_env,
                "version": __version__,
                "release": settings.release_version,
                "color": settings.release_color,
            }
        )

    @app.get("/readyz", tags=["system"], summary="就绪检查")
    async def readyz() -> object:
        # 同步探针（驱动都是同步的）放到线程里跑，避免阻塞事件循环
        report = await asyncio.to_thread(check_readiness)
        ready = report["status"] != "unavailable"
        return ok(
            report,
            message="ok" if ready else "unavailable",
            status_code=200 if ready else 503,
        )

    return app


def _verify_parsing_engines() -> None:
    """启动自检：引擎依赖缺失时立刻失败，而不是等用户上传文件才 500。"""
    from app.parsing import engine_names

    names = engine_names()
    if not names:
        raise RuntimeError("解析引擎注册表为空：app.parsing.engines 未被导入")
    logging.getLogger(__name__).info("解析引擎就绪: %s", ", ".join(names))


def _log_recovery_config() -> None:
    """把这些互相牵制的阈值打出来：出问题时第一眼看日志就能判断是否配错。"""
    logging.getLogger(__name__).info(
        "任务重试与看护配置: auto_retry=%s/%ss downgrade=%s(x%s,min=%s) revive=%s "
        "heartbeat=%ss stale=%ss watchdog=%s(%ss) "
        "time_limit parse=%s/%ss extract=%s/%ss lock=%s/%ss visibility=%ss",
        settings.task_auto_retry_max_attempts,
        settings.task_auto_retry_backoff_seconds,
        settings.task_downgrade_max_attempts,
        settings.task_downgrade_batch_ratio,
        settings.task_downgrade_batch_min,
        settings.task_manual_revive_max_attempts,
        settings.task_heartbeat_ttl_seconds,
        settings.task_stale_threshold_seconds,
        settings.task_watchdog_enabled,
        settings.task_watchdog_interval_seconds,
        settings.parse_soft_time_limit_seconds,
        settings.parse_hard_time_limit_seconds,
        settings.extract_soft_time_limit_seconds,
        settings.extract_hard_time_limit_seconds,
        settings.parse_run_lock_ttl_seconds,
        settings.extract_run_lock_ttl_seconds,
        settings.celery_visibility_timeout_seconds,
    )


def _register_routers(app: FastAPI) -> None:
    from app.api.v1.router import api_router

    app.include_router(api_router, prefix=settings.api_prefix)


app = create_app()
