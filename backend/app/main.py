"""FastAPI 应用装配：中间件、异常处理器、路由挂载。"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import RequestIdMiddleware, configure_logging
from app.core.response import EnvelopeMiddleware, ok


def create_app() -> FastAPI:
    configure_logging(logging.DEBUG if settings.debug else logging.INFO)

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

    @app.get("/healthz", tags=["system"], summary="健康检查")
    async def healthz() -> object:
        return ok({"status": "ok", "env": settings.app_env, "version": __version__})

    return app


def _register_routers(app: FastAPI) -> None:
    from app.api.v1.router import api_router

    app.include_router(api_router, prefix=settings.api_prefix)


app = create_app()
