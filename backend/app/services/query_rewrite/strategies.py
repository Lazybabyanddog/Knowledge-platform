"""策略执行器。

两类语义严格区分：
- ``text``：串行改写主查询，后一个拿到前一个的输出；
- ``expand``：不动主查询，只产出额外检索查询。

单个策略失败只标记自己降级，不打断整次运行。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.infra.llm import prompts
from app.infra.llm.provider import LLMError
from app.services.query_rewrite.budget import Budget, BudgetExceeded
from app.services.query_rewrite.specs import StrategySpec
from app.services.query_rewrite.understanding import Understanding

logger = logging.getLogger(__name__)

LLMCaller = Callable[[str, str], dict[str, Any]]
EntityLookup = Callable[[str], list[dict[str, Any]]]

STATUS_OK = "ok"
STATUS_SKIPPED = "skipped"
STATUS_DEGRADED = "degraded"


@dataclass(slots=True)
class Outcome:
    code: str
    status: str
    text_in: str
    text_out: str
    reason: str = ""
    extra_queries: list[str] = field(default_factory=list)
    llm_calls: int = 0
    elapsed_ms: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class StrategyRunner:
    def __init__(
        self,
        *,
        budget: Budget,
        call_llm: LLMCaller | None,
        entity_lookup: EntityLookup | None,
    ) -> None:
        self.budget = budget
        self._call_llm = call_llm
        self._entity_lookup = entity_lookup

    def execute(
        self,
        spec: StrategySpec,
        text: str,
        *,
        history: list[dict[str, str]],
        understanding: Understanding,
    ) -> Outcome:
        started = time.perf_counter()
        before_calls = self.budget.llm_calls
        try:
            outcome = self._dispatch(spec, text, history=history, understanding=understanding)
        except BudgetExceeded as exc:
            outcome = Outcome(
                code=spec.code,
                status=STATUS_DEGRADED,
                text_in=text,
                text_out=text,
                reason=str(exc),
            )
        except LLMError as exc:
            logger.info("策略 %s 模型调用失败: %s", spec.code, exc)
            outcome = Outcome(
                code=spec.code,
                status=STATUS_DEGRADED,
                text_in=text,
                text_out=text,
                reason=f"模型调用失败: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - 单策略失败不拖垮整次运行
            logger.exception("策略 %s 执行异常", spec.code)
            outcome = Outcome(
                code=spec.code,
                status=STATUS_DEGRADED,
                text_in=text,
                text_out=text,
                reason=f"执行异常: {exc}",
            )
        outcome.llm_calls = self.budget.llm_calls - before_calls
        outcome.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    def _dispatch(
        self,
        spec: StrategySpec,
        text: str,
        *,
        history: list[dict[str, str]],
        understanding: Understanding,
    ) -> Outcome:
        if not spec.enabled:
            return _skipped(spec.code, text, "当前版本未启用")
        if spec.code == "context":
            return self._context(spec, text, history)
        if spec.code == "entity":
            return self._entity(spec, text, understanding)
        if spec.code == "semantic":
            return self._semantic(spec, text)
        if spec.code == "decompose":
            return self._expand(
                spec, text, prompts.DECOMPOSE_SYSTEM, prompts.decompose_prompt(text)
            )
        if spec.code == "multi":
            return self._expand(
                spec, text, prompts.MULTI_QUERY_SYSTEM, prompts.multi_query_prompt(text)
            )
        return _skipped(spec.code, text, "当前版本未启用")

    # ---- 文本改写类 ----

    def _context(
        self, spec: StrategySpec, text: str, history: list[dict[str, str]]
    ) -> Outcome:
        if not history:
            return _skipped(spec.code, text, "缺少对话历史，无法消解指代")
        payload = self._llm(
            prompts.CONTEXT_REWRITE_SYSTEM, prompts.context_rewrite_prompt(text, history)
        )
        rewritten = _first_text(payload.get("query"))
        if not rewritten or _same(rewritten, text):
            return _skipped(spec.code, text, "模型未产出有效改写")
        return _ok(spec.code, text, rewritten, _first_text(payload.get("reason")))

    def _semantic(self, spec: StrategySpec, text: str) -> Outcome:
        payload = self._llm(prompts.SEMANTIC_REWRITE_SYSTEM, prompts.semantic_rewrite_prompt(text))
        rewritten = _first_text(payload.get("query"))
        if not rewritten or _same(rewritten, text):
            return _skipped(spec.code, text, "改写结果与原查询等价")
        return _ok(spec.code, text, rewritten, _first_text(payload.get("reason")))

    def _entity(
        self, spec: StrategySpec, text: str, understanding: Understanding
    ) -> Outcome:
        mentions = understanding.signals.entity_candidates[:2]
        if not mentions:
            return _skipped(spec.code, text, "未发现需要标准化的别名")
        if self._entity_lookup is None:
            return _skipped(spec.code, text, "图谱不可用，跳过实体标准化")
        resolved: list[dict[str, Any]] = []
        current = text
        for raw in mentions:
            try:
                candidates = self._entity_lookup(raw)
            except Exception as exc:  # noqa: BLE001 - 图谱异常等同于不可用
                logger.info("实体候选查询失败: %s", exc)
                return _skipped(spec.code, text, "图谱查询失败，跳过实体标准化")
            if not candidates:
                continue
            chosen = candidates[0] if len(candidates) == 1 else self._disambiguate(raw, candidates)
            name = _first_text(chosen.get("name"))
            if not name:
                continue
            current = current.replace(raw, name)
            resolved.append(
                {
                    "raw": raw,
                    "entity_id": chosen.get("id"),
                    "name": name,
                    "confidence": float(chosen.get("confidence") or 1.0),
                    "candidates": len(candidates),
                }
            )
        if not resolved:
            return _skipped(spec.code, text, "图谱中没有匹配到候选实体")
        return Outcome(
            code=spec.code,
            status=STATUS_OK,
            text_in=text,
            text_out=current,
            reason=f"标准化 {len(resolved)} 个实体",
            details={"resolved": resolved},
        )

    def _disambiguate(self, raw: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        payload = self._llm(
            prompts.ENTITY_DISAMBIGUATE_SYSTEM, prompts.entity_disambiguate_prompt(raw, candidates)
        )
        target = _first_text(payload.get("entity_id"))
        for item in candidates:
            if str(item.get("id")) == target:
                return item
        return candidates[0]

    # ---- 召回扩展类 ----

    def _expand(self, spec: StrategySpec, text: str, system: str, user: str) -> Outcome:
        payload = self._llm(system, user)
        raw_items = payload.get("sub_questions") or payload.get("queries")
        queries: list[str] = []
        for entry in raw_items if isinstance(raw_items, list) else []:
            value = _first_text(entry)
            if value and not _same(value, text) and value not in queries:
                queries.append(value)
        queries = queries[:4]
        if spec.code == "decompose" and len(queries) < 2:
            return _skipped(spec.code, text, "拆解结果不足 2 个可独立检索的子问题")
        if spec.code == "multi" and not queries:
            return _skipped(spec.code, text, "模型未产出有效的扩展查询")
        self.budget.take_queries(len(queries))
        return Outcome(
            code=spec.code,
            status=STATUS_OK,
            text_in=text,
            text_out=text,
            reason=_first_text(payload.get("reason")) or f"产出 {len(queries)} 条扩展查询",
            extra_queries=queries,
        )

    def _llm(self, system: str, user: str) -> dict[str, Any]:
        if self._call_llm is None:
            raise BudgetExceeded("未配置 LLM，无法执行需要模型能力的策略")
        # 先占预算再调用：超时/超预算时不发出请求
        self.budget.take_llm()
        return self._call_llm(system, user)


def _ok(code: str, text_in: str, text_out: str, reason: str) -> Outcome:
    return Outcome(
        code=code,
        status=STATUS_OK,
        text_in=text_in,
        text_out=text_out,
        reason=reason,
    )


def _skipped(code: str, text: str, reason: str) -> Outcome:
    return Outcome(code=code, status=STATUS_SKIPPED, text_in=text, text_out=text, reason=reason)


def _first_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _same(left: str, right: str) -> bool:
    return _normalize(left) == _normalize(right)


def _normalize(text: str) -> str:
    return " ".join(text.split()).strip()


__all__ = [
    "EntityLookup",
    "LLMCaller",
    "Outcome",
    "STATUS_DEGRADED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "StrategyRunner",
]
