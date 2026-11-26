"""切块配置：解析策略 ``config.chunk`` 的唯一解析与校验入口。

切块参数挂在解析策略的版本里，因此"用哪套切块规则"天然可追溯：
``document_chunk.parse_task_id -> parse_task.strategy_id -> strategy.config.chunk``。

缺省行为必须与能力上线前完全一致：没有 ``chunk`` 子对象时按 ``block_merge`` +
``chunk_hint`` 切分，存量策略不需要改动。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_CHUNK_SIZE = 800
MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 4000

BLOCK_MERGE = "block_merge"
FIXED_WINDOW = "fixed_window"
HEADING_SECTION = "heading_section"
CHUNK_STRATEGIES = (BLOCK_MERGE, FIXED_WINDOW, HEADING_SECTION)


class ChunkConfigError(ValueError):
    """切块配置不合法；请求校验层据此返回 400 并指出字段。"""


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    strategy: str = BLOCK_MERGE
    size: int = DEFAULT_CHUNK_SIZE
    overlap: int = 0


def parse_chunk_config(config: Mapping[str, Any] | None) -> ChunkConfig:
    """把策略的 ``config`` 解析成切块配置，非法输入抛 ``ChunkConfigError``。"""
    data: Mapping[str, Any] = config if isinstance(config, Mapping) else {}
    raw = data.get("chunk")
    hint = data.get("chunk_hint")

    if raw is None:
        return ChunkConfig(size=_size_from_hint(hint))
    if not isinstance(raw, Mapping):
        raise ChunkConfigError(
            'chunk 必须是对象，例如 {"strategy": "fixed_window", "size": 600, "overlap": 80}'
        )

    strategy = str(raw.get("strategy") or BLOCK_MERGE).strip()
    if strategy not in CHUNK_STRATEGIES:
        raise ChunkConfigError(
            f"不支持的切块策略: {strategy}（可选 {', '.join(CHUNK_STRATEGIES)}）"
        )

    size = (
        _require_int(raw.get("size"), "chunk.size")
        if raw.get("size") is not None
        else _size_from_hint(hint)
    )
    if not MIN_CHUNK_SIZE <= size <= MAX_CHUNK_SIZE:
        raise ChunkConfigError(
            f"chunk.size 必须在 {MIN_CHUNK_SIZE} 到 {MAX_CHUNK_SIZE} 之间，当前 {size}"
        )

    overlap = (
        _require_int(raw.get("overlap"), "chunk.overlap")
        if raw.get("overlap") is not None
        else 0
    )
    if overlap < 0:
        raise ChunkConfigError("chunk.overlap 不能为负数")
    if strategy != FIXED_WINDOW and overlap:
        raise ChunkConfigError(
            f"chunk.overlap 只适用于 {FIXED_WINDOW}，当前策略为 {strategy}"
        )
    if overlap > size // 2:
        raise ChunkConfigError(f"chunk.overlap 不能超过 chunk.size 的一半（{size // 2}）")

    return ChunkConfig(strategy=strategy, size=size, overlap=overlap)


def chunk_config_view(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """给接口用的生效切块配置；库里的历史脏数据不报错，只返回空对象。"""
    try:
        cfg = parse_chunk_config(config)
    except ChunkConfigError:
        return {}
    return {"strategy": cfg.strategy, "size": cfg.size, "overlap": cfg.overlap}


def _require_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ChunkConfigError(f"{field} 必须是整数")
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ChunkConfigError(f"{field} 必须是整数") from exc


def _size_from_hint(value: Any) -> int:
    """长度缺省时回退到历史字段 ``chunk_hint``，再回退到默认值；脏值不报错。"""
    if value is None or isinstance(value, bool):
        return DEFAULT_CHUNK_SIZE
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return DEFAULT_CHUNK_SIZE
    return number if number > 0 else DEFAULT_CHUNK_SIZE


__all__ = [
    "BLOCK_MERGE",
    "CHUNK_STRATEGIES",
    "ChunkConfig",
    "ChunkConfigError",
    "DEFAULT_CHUNK_SIZE",
    "FIXED_WINDOW",
    "HEADING_SECTION",
    "MAX_CHUNK_SIZE",
    "MIN_CHUNK_SIZE",
    "chunk_config_view",
    "parse_chunk_config",
]
