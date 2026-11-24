"""一次运行的成本与时间上限。

同步的模型调用无法被可靠中断，所以控制点放在"派发之前"：超时或超预算就不再派发，
已完成的对照组结果照常返回。
"""

from __future__ import annotations

import time
from collections.abc import Callable


class BudgetExceeded(RuntimeError):
    """准备派发的工作超出本次运行的预算或已经超时。"""


class Budget:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_llm_calls: int,
        max_queries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.started = clock()
        self.deadline = self.started + max(0.1, timeout_seconds)
        self.max_llm_calls = max(0, max_llm_calls)
        self.max_queries = max(0, max_queries)
        self.llm_calls = 0
        self.embed_calls = 0
        self.retrieval_calls = 0
        self.queries_used = 0
        self._clock = clock

    def expired(self) -> bool:
        return self._clock() >= self.deadline

    def elapsed_ms(self) -> int:
        return int((self._clock() - self.started) * 1000)

    def take_llm(self) -> None:
        if self.expired():
            raise BudgetExceeded("运行已超时")
        if self.llm_calls >= self.max_llm_calls:
            raise BudgetExceeded("模型调用预算已用尽")
        self.llm_calls += 1

    def take_queries(self, count: int) -> None:
        if count <= 0:
            return
        if self.expired():
            raise BudgetExceeded("运行已超时")
        if self.queries_used + count > self.max_queries:
            raise BudgetExceeded("检索次数已达上限")
        self.queries_used += count

    def mark_retrieval(self, count: int) -> None:
        self.retrieval_calls += max(0, count)
        self.embed_calls += 1 if count else 0


__all__ = ["Budget", "BudgetExceeded"]
