"""索引构建期的入口：扫描 -> 判定 -> 快照，一步产出可直接落库的词典。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.services.glossary.builtin import BUILTIN_VERSION
from app.services.glossary.config import AliasConfig, resolve_rules
from app.services.glossary.extract import scan
from app.services.glossary.judge import judge
from app.services.glossary.snapshot import serialize


def build_glossary(blocks: Iterable[Any], config: AliasConfig) -> dict[str, Any]:
    """对整份文档的解析块跑一遍规则；纯计算，不碰数据库。"""
    rules = resolve_rules(config)
    hits = scan(blocks, rules)
    entries, dropped = judge(hits)
    return serialize(
        entries,
        rules,
        raw_hits=len(hits),
        dropped=dropped,
        builtin_version=BUILTIN_VERSION if config.use_builtin else None,
    )


__all__ = ["build_glossary"]
