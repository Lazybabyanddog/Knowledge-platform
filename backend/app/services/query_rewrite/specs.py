"""策略注册表。

``kind`` 决定执行语义：``text`` 串行改写主查询，``expand`` 只产出额外检索查询。
``enabled=False`` 的策略照样出现在决策表与面板里，只是不会被派发——
"未启用"落在数据结构上，而不是散落在各处的 if。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StrategyKind = Literal["text", "expand"]


@dataclass(frozen=True, slots=True)
class StrategySpec:
    code: str
    label: str
    kind: StrategyKind
    requires_llm: bool
    priority: int
    requires_graph: bool = False
    enabled: bool = True
    summary: str = ""


STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        code="context",
        label="上下文改写",
        kind="text",
        requires_llm=True,
        priority=1,
        summary="按对话历史补全省略的主语与限定条件",
    ),
    StrategySpec(
        code="entity",
        label="实体标准化",
        kind="text",
        requires_llm=True,
        priority=2,
        requires_graph=True,
        summary="把查询里的别名对齐到图谱中的标准实体名",
    ),
    StrategySpec(
        code="semantic",
        label="语义改写",
        kind="text",
        requires_llm=True,
        priority=3,
        summary="去掉口语，改写成检索友好的表达",
    ),
    StrategySpec(
        code="decompose",
        label="问题拆解",
        kind="expand",
        requires_llm=True,
        priority=4,
        summary="把复合问题拆成可独立检索的子问题",
    ),
    StrategySpec(
        code="multi",
        label="Multi-Query",
        kind="expand",
        requires_llm=True,
        priority=5,
        summary="从不同表达角度扩展同一条信息需求",
    ),
    StrategySpec(
        code="stepback",
        label="Step-back",
        kind="expand",
        requires_llm=True,
        priority=6,
        enabled=False,
        summary="先抽象成上位问题再检索（当前版本未启用）",
    ),
    StrategySpec(
        code="hyde",
        label="HyDE",
        kind="expand",
        requires_llm=True,
        priority=7,
        enabled=False,
        summary="先生成假设性答案再检索（当前版本未启用）",
    ),
)

SPECS_BY_CODE: dict[str, StrategySpec] = {spec.code: spec for spec in STRATEGIES}

DEFAULT_ALLOWLIST: tuple[str, ...] = tuple(spec.code for spec in STRATEGIES if spec.enabled)


def get_spec(code: str) -> StrategySpec:
    return SPECS_BY_CODE[code]


def all_specs() -> tuple[StrategySpec, ...]:
    return STRATEGIES


__all__ = [
    "DEFAULT_ALLOWLIST",
    "SPECS_BY_CODE",
    "STRATEGIES",
    "StrategyKind",
    "StrategySpec",
    "all_specs",
    "get_spec",
]
