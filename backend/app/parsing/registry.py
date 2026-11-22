"""引擎注册表：策略里的 ``engine`` 字段通过它解析为具体实现。"""

from __future__ import annotations

from collections.abc import Iterable

from app.parsing.base import ParseEngine, UnknownEngineError

_ENGINES: dict[str, ParseEngine] = {}


def register_engine(engine_cls: type[ParseEngine]) -> type[ParseEngine]:
    """类装饰器：实例化一次并登记，重复注册同名引擎直接覆盖（便于测试替换）。"""
    if not getattr(engine_cls, "name", ""):
        raise ValueError(f"{engine_cls.__name__} 缺少 name")
    _ENGINES[engine_cls.name] = engine_cls()
    return engine_cls


def get_engine(name: str) -> ParseEngine:
    engine = _ENGINES.get(name)
    if engine is None:
        raise UnknownEngineError(f"未注册的解析引擎: {name}（可用: {', '.join(sorted(_ENGINES))}）")
    return engine


def has_engine(name: str) -> bool:
    return name in _ENGINES


def engine_names() -> list[str]:
    return sorted(_ENGINES)


def engines_for(doc_type: str | None = None) -> list[ParseEngine]:
    values: Iterable[ParseEngine] = _ENGINES.values()
    if doc_type:
        return [engine for engine in values if engine.supports(doc_type)]
    return list(values)


