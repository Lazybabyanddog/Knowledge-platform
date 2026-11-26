"""引擎选择规则：策略校验、图节点、队列路由共用同一套判断。

三个调用方各写一份的话，迟早出现"图里回落到 pdf_layout，队列却按 mineru 投递"
这种不一致，因此把"策略指定的引擎是否可用"收敛到这里。
"""

from __future__ import annotations

from app.parsing.base import ParseEngine
from app.parsing.registry import get_engine, has_engine

# 各文档类型的兜底引擎；与解析图的节点配置保持一致
FALLBACK_ENGINE_BY_DOC_TYPE = {
    "pdf": "pdf_layout",
    "office": "office",
    "image": "ocr",
    "text": "plain_text",
    "html": "html_dom",
}
DEFAULT_FALLBACK_ENGINE = "plain_text"


def fallback_engine_name(doc_type: str | None) -> str:
    return FALLBACK_ENGINE_BY_DOC_TYPE.get(doc_type or "", DEFAULT_FALLBACK_ENGINE)


def resolve_effective_engine(
    doc_type: str, engine_name: str | None, fallback: str | None = None
) -> ParseEngine:
    """策略指定的引擎可用就用它，否则回落该文档类型的兜底引擎。

    "可用"= 已注册且支持该文档类型；与图节点此前的行为一致，只是不再各写一遍。
    """
    requested = (engine_name or "").strip()
    if requested and has_engine(requested):
        engine = get_engine(requested)
        if engine.supports(doc_type):
            return engine
    return get_engine(fallback or fallback_engine_name(doc_type))


def resolve_effective_engine_name(doc_type: str, engine_name: str | None) -> str:
    """只要名字的场合（如按引擎选队列）用它，避免调用方再拆一次规则。"""
    return resolve_effective_engine(doc_type, engine_name).name


__all__ = [
    "DEFAULT_FALLBACK_ENGINE",
    "FALLBACK_ENGINE_BY_DOC_TYPE",
    "fallback_engine_name",
    "resolve_effective_engine",
    "resolve_effective_engine_name",
]
