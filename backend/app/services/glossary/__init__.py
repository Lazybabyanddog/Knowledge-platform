"""文档级别名词典：索引时自算，只在生成阶段注入。

与知识图谱的实体抽取是两条独立的线：这里只回答"这篇文档里这个短名指谁"，
不产实体、不做消歧、不写 ``document_chunk.content``、不参与向量化。
"""

from __future__ import annotations

from app.services.glossary.config import (
    AliasConfig,
    AliasConfigError,
    alias_config_view,
    parse_alias_config,
)
from app.services.glossary.inject import (
    MAX_LINES_PER_HIT,
    GlossaryLine,
    attach_glossary,
    render_glossary,
)
from app.services.glossary.pipeline import build_glossary
from app.services.glossary.snapshot import STATUS_DEGRADED, glossary_view

__all__ = [
    "MAX_LINES_PER_HIT",
    "STATUS_DEGRADED",
    "AliasConfig",
    "AliasConfigError",
    "GlossaryLine",
    "alias_config_view",
    "attach_glossary",
    "build_glossary",
    "glossary_view",
    "parse_alias_config",
    "render_glossary",
]
