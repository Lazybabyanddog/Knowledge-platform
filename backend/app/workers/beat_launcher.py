"""Beat 单实例启动器。

蓝绿两色都会尝试启动 beat，但 ``watchdog.sweep`` 每一周期只能有一个来源。
抢到 Redis 锁的实例拉起 celery beat，抢不到的立即退出；
锁按 TTL 续租，持有者进程消失后锁自然过期，下一次启动能接管。
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from types import FrameType

from app.core.config import settings
from app.infra.cache.redis_client import get_redis

LOCK_KEY = "release:beat:lock"

# 只在 owner 匹配时续租，避免锁过期后被别人拿到却继续按自己的 TTL 续
_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

_BEAT_COMMAND = [
    sys.executable,
    "-m",
    "celery",
    "-A",
    "app.workers.celery_app:celery_app",
    "beat",
    "-l",
    "info",
]

logger = logging.getLogger(__name__)


def owner_id() -> str:
    """主机名 + pid + 秒级时间戳：同一容器重启后不会复用上一个 owner。"""
    return f"{socket.gethostname()}:{os.getpid()}:{int(time.time())}"


def acquire(redis, owner: str, ttl_ms: int) -> bool:  # noqa: ANN001 - Redis 客户端类型多变
    return bool(redis.set(LOCK_KEY, owner, nx=True, px=ttl_ms))


def renew(redis, owner: str, ttl_ms: int) -> bool:  # noqa: ANN001
    return bool(redis.eval(_RENEW_SCRIPT, 1, LOCK_KEY, owner, ttl_ms))


def release(redis, owner: str) -> None:  # noqa: ANN001
    redis.eval(_RELEASE_SCRIPT, 1, LOCK_KEY, owner)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    redis = get_redis()
    owner = owner_id()
    ttl_ms = max(settings.beat_lock_ttl_seconds, 5) * 1000

    if not acquire(redis, owner, ttl_ms):
        holder = redis.get(LOCK_KEY)
        logger.warning(
            "beat 锁已由 %s 持有，颜色 %s 不启动 beat（这是预期行为，不是故障）",
            holder,
            settings.release_color,
        )
        return 0

    logger.info("beat 锁获取成功 owner=%s color=%s", owner, settings.release_color)
    stop = threading.Event()
    lost = threading.Event()
    proc = subprocess.Popen(_BEAT_COMMAND)  # noqa: S603 - 命令是固定的常量列表

    def _renew_loop() -> None:
        interval = ttl_ms / 3000.0
        while not stop.wait(interval):
            try:
                if renew(redis, owner, ttl_ms):
                    continue
            except Exception as exc:  # noqa: BLE001 - 续租失败不该让 beat 直接停
                logger.warning("beat 锁续租失败: %s", exc)
                continue
            logger.error("beat 锁已丢失，停止本实例避免与新的持有者重复触发")
            lost.set()
            proc.terminate()
            return

    renewer = threading.Thread(target=_renew_loop, name="beat-lock-renewer", daemon=True)
    renewer.start()

    def _shutdown(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
        logger.info("收到信号 %s，停止 beat", signum)
        stop.set()
        proc.terminate()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    exit_code = proc.wait()
    stop.set()
    renewer.join(timeout=2)
    release(redis, owner)
    logger.info("beat 退出 code=%s lost_lock=%s", exit_code, lost.is_set())
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
