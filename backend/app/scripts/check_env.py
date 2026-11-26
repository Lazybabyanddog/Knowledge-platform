"""启动自检入口：``python -m app.scripts.check_env``。

容器启动与发布冒烟都调用它，把"镜像里缺什么"在启动阶段暴露，
而不是等用户上传一个 PDF 才 500。输出会被写进发布记录，所以凭据一律脱敏。
"""

from __future__ import annotations

import json
import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app import __version__
from app.core.config import settings


def mask_url(url: str) -> str:
    """把 URL 里的密码替换为 ***。"""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = f"{parts.username}:***@{parts.hostname}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def build_report() -> dict[str, Any]:
    from app.parsing import engine_names

    return {
        "version": __version__,
        "release": settings.release_version,
        "color": settings.release_color,
        "env": settings.app_env,
        "engines": engine_names(),
        "config": {
            "api_prefix": settings.api_prefix,
            "database_url": mask_url(settings.database_url),
            "redis_url": mask_url(settings.redis_url),
            "celery_broker_url": mask_url(settings.celery_broker_url),
            "neo4j_uri": settings.neo4j_uri,
            "minio_endpoint": settings.minio_endpoint,
            "minio_bucket": settings.minio_bucket,
            "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
        },
    }


def main() -> int:
    try:
        report = build_report()
    except Exception as exc:  # noqa: BLE001 - 自检要把任何导入失败变成可读信息
        print(f"启动自检失败: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["engines"]:
        print("启动自检失败: 解析引擎注册表为空，app.parsing.engines 未被导入", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
