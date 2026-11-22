"""解析引擎适配层：导入本包即完成内置引擎注册。"""

from app.parsing import engines  # noqa: F401  触发引擎注册
from app.parsing.base import (
    EngineResult,
    ParsedBlock,
    ParseEngine,
    ParseError,
    ParseInput,
    UnknownEngineError,
)
from app.parsing.quality import QualityResult, score_quality
from app.parsing.registry import (
    engine_names,
    engines_for,
    get_engine,
    has_engine,
    register_engine,
)
from app.parsing.text_blocks import detect_doc_type

__all__ = [
    "EngineResult",
    "ParseEngine",
    "ParseError",
    "ParseInput",
    "ParsedBlock",
    "QualityResult",
    "UnknownEngineError",
    "detect_doc_type",
    "engine_names",
    "engines_for",
    "get_engine",
    "has_engine",
    "register_engine",
    "score_quality",
]
