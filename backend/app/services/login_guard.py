"""登录失败计数与锁定。

计数放 Redis，不落库：登录是高频路径，写库会放大 DB 压力；
键自带 TTL，锁定窗口过期后自动解锁，无需清理任务。
"""

from __future__ import annotations

import math

from redis import Redis

from app.core.config import settings
from app.core.exceptions import TooManyRequestsError
from app.infra.cache.redis_client import get_redis

KEY_PREFIX = "login:fail:"


class LoginGuard:
    def __init__(self, client: Redis | None = None) -> None:
        self.client = client or get_redis()

    def _key(self, username: str) -> str:
        return f"{KEY_PREFIX}{username.strip().lower()}"

    def failures(self, username: str) -> int:
        return int(self.client.get(self._key(username)) or 0)

    def ensure_not_locked(self, username: str) -> None:
        key = self._key(username)
        attempts = int(self.client.get(key) or 0)
        if attempts < settings.login_max_failures:
            return

        ttl = int(self.client.ttl(key) or 0)
        retry_after = max(ttl, 1)
        raise TooManyRequestsError(
            f"登录失败次数过多，请在 {math.ceil(retry_after / 60)} 分钟后重试",
            data={"retry_after_seconds": retry_after, "failures": attempts},
        )

    def record_failure(self, username: str) -> int:
        key = self._key(username)
        attempts = int(self.client.incr(key))
        if attempts == 1:
            # 首次失败开启窗口，此后 10 分钟内的失败都计入同一窗口
            self.client.expire(key, settings.login_lock_minutes * 60)
        return attempts

    def reset(self, username: str) -> None:
        self.client.delete(self._key(username))
