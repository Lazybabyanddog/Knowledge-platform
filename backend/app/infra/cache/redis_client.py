"""Redis 客户端单例。"""

from __future__ import annotations

from functools import lru_cache

from redis import Redis

from app.core.config import settings


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


def reset_redis_cache() -> None:
    """测试或配置热更新时清掉单例。"""
    get_redis.cache_clear()
