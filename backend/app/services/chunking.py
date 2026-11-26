"""把解析块合并成可检索片段。

三种策略共同的不变量：

- 被标记丢弃的块与空块不参与合并；
- 表格块整体保留，独立成片段，不与正文合并；
- 标题块不单独成片段，作为其后片段的上下文前缀（标题栈）；
- 片段内容包含当前标题路径，``char_count`` 计入这段前缀。

策略之间的差别只在**边界怎么定**：

- ``block_merge``（默认）：相邻正文块合并到长度上限，沿用能力上线前的行为；
- ``fixed_window``：按窗口累积，支持 ``overlap`` 重叠，只在段落/句末边界收尾；
- ``heading_section``：以标题为边界成节，节内超长再按窗口二次切分。

本模块是纯函数，不碰数据库，便于单测。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import BlockType
from app.services.chunk_config import (
    BLOCK_MERGE,
    DEFAULT_CHUNK_SIZE,
    FIXED_WINDOW,
    HEADING_SECTION,
    ChunkConfig,
)

DEFAULT_CHUNK_HINT = DEFAULT_CHUNK_SIZE
_SENTENCE_ENDS = ("。", "！", "？", "；", "\n", ".", "!", "?", ";")
_HEADING_GAP = " / "


@dataclass(slots=True)
class ChunkDraft:
    """一个待持久化的片段，字段与 ``document_chunk`` 一一对应。"""

    seq: int
    content: str
    heading_path: str | None
    block_ids: list[str] = field(default_factory=list)
    first_page_no: int | None = None
    last_page_no: int | None = None
    char_count: int = 0


def _block_type(block: Any) -> str:
    raw = getattr(block, "block_type", "")
    return str(getattr(raw, "value", raw))


def _block_id(block: Any) -> str:
    return str(getattr(block, "id", ""))


def _block_text(block: Any) -> str:
    return str(getattr(block, "content", "") or "").strip()


def _page_no(block: Any) -> int | None:
    value = getattr(block, "page_no", None)
    return value if isinstance(value, int) else None


class _HeadingStack:
    """维护「当前所处标题路径」，用于给片段加前缀。"""

    def __init__(self) -> None:
        self._items: list[tuple[int, str, str]] = []

    def push(self, level: int | None, text: str, block_id: str) -> None:
        depth = level if isinstance(level, int) and level > 0 else 1
        while self._items and self._items[-1][0] >= depth:
            self._items.pop()
        self._items.append((depth, text, block_id))

    def path(self) -> str | None:
        if not self._items:
            return None
        return _HEADING_GAP.join(text for _, text, _ in self._items)

    def block_ids(self) -> list[str]:
        """标题块也是片段的来源，引用回原文时需要它。"""
        return [block_id for _, _, block_id in self._items]


def split_long_text(text: str, limit: int) -> list[str]:
    """把超长文本切成不超过 ``limit`` 的若干段，优先在空行与句末断开。"""
    if limit <= 0:
        return [text]
    if len(text) <= limit:
        return [text]

    pieces: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal buffer
        if buffer:
            pieces.append(buffer)
            buffer = ""

    for paragraph in text.split("\n\n"):
        if not paragraph:
            continue
        flush()  # 段落之间不合并，保留原文的分段边界
        for sentence in _split_sentences(paragraph):
            # 单句仍超限（无标点的长串）只能硬切，这是三级回退的最后一档
            while len(sentence) > limit:
                flush()
                pieces.append(sentence[:limit])
                sentence = sentence[limit:]
            if len(buffer) + len(sentence) > limit:
                flush()
            buffer += sentence
    flush()
    return pieces


def _split_sentences(paragraph: str) -> list[str]:
    """按句末标点切句；单句仍超限时由调用方硬切。"""
    result: list[str] = []
    buffer = ""
    for char in paragraph:
        buffer += char
        if char in _SENTENCE_ENDS:
            result.append(buffer)
            buffer = ""
    if buffer:
        result.append(buffer)
    return result


def build_chunks(
    blocks: Iterable[Any],
    config: int | ChunkConfig | None = None,
    *,
    chunk_hint: int | None = None,
) -> list[ChunkDraft]:
    """按策略把有序的块切成片段。

    ``config`` 兼容旧的 ``int`` 写法（等价 ``block_merge`` + 该长度），
    ``chunk_hint`` 关键字保留给既有调用点，因此不需要批量改调用方。
    """
    cfg = _coerce_config(chunk_hint if chunk_hint is not None else config)
    strategy: Callable[[list[Any], ChunkConfig], list[ChunkDraft]] = _STRATEGIES[cfg.strategy]
    return strategy(list(blocks), cfg)


def _coerce_config(config: int | ChunkConfig | None) -> ChunkConfig:
    if isinstance(config, ChunkConfig):
        return config
    if isinstance(config, bool):
        return ChunkConfig()
    if isinstance(config, int):
        return ChunkConfig(size=config if config > 0 else DEFAULT_CHUNK_SIZE)
    return ChunkConfig()


def _content_with_heading(text: str, heading: str | None) -> str:
    return f"{heading}\n{text}" if heading else text


def _body_limit(cfg: ChunkConfig, heading: str | None) -> int:
    """正文可用预算；标题前缀计入 ``size``，避免片段实际长度超限。"""
    if not heading:
        return cfg.size
    return max(1, cfg.size - len(heading) - 1)


def _emit_single(
    drafts: list[ChunkDraft], headings: _HeadingStack, text: str, block: Any
) -> None:
    """单块独立成片段（表格、超长块的前几段）。"""
    heading = headings.path()
    content = _content_with_heading(text, heading)
    page = _page_no(block)
    drafts.append(
        ChunkDraft(
            seq=len(drafts),
            content=content,
            heading_path=heading,
            block_ids=headings.block_ids() + [_block_id(block)],
            first_page_no=page,
            last_page_no=page,
            char_count=len(content),
        )
    )


def _block_merge(blocks: list[Any], cfg: ChunkConfig) -> list[ChunkDraft]:
    """能力上线前的行为，逐字保留：标题做前缀、表格独立、超长三级回退。"""
    hint = cfg.size
    headings = _HeadingStack()
    drafts: list[ChunkDraft] = []

    pending: list[Any] = []
    pending_len = 0

    def flush() -> None:
        nonlocal pending, pending_len
        if not pending:
            return
        body = "\n".join(_block_text(block) for block in pending)
        heading = headings.path()
        content = _content_with_heading(body, heading)
        pages = [p for p in (_page_no(block) for block in pending) if p is not None]
        drafts.append(
            ChunkDraft(
                seq=len(drafts),
                content=content,
                heading_path=heading,
                block_ids=headings.block_ids() + [_block_id(block) for block in pending],
                first_page_no=min(pages) if pages else None,
                last_page_no=max(pages) if pages else None,
                char_count=len(content),
            )
        )
        pending = []
        pending_len = 0

    for block in blocks:
        if getattr(block, "is_discarded", False):
            continue
        text = _block_text(block)
        if not text:
            continue

        kind = _block_type(block)
        if kind == BlockType.TITLE.value:
            flush()
            headings.push(getattr(block, "level", None), text, _block_id(block))
            continue

        if kind == BlockType.TABLE.value:
            flush()
            _emit_single(drafts, headings, text, block)
            continue

        if pending_len + len(text) > hint:
            flush()

        if len(text) > hint:
            # 单块超长：整段切分，前面的部分独立成片段，末尾继续参与累积
            pieces = split_long_text(text, hint)
            for piece in pieces[:-1]:
                _emit_single(drafts, headings, piece, block)
            tail = pieces[-1]
            pending.append(_TailBlock(block, tail))
            pending_len = len(tail)
            continue

        pending.append(block)
        pending_len += len(text)

    flush()
    return drafts


def _fixed_window(blocks: list[Any], cfg: ChunkConfig) -> list[ChunkDraft]:
    headings = _HeadingStack()
    drafts: list[ChunkDraft] = []

    pending: list[Any] = []
    pending_len = 0
    carry_only = False

    def flush() -> None:
        nonlocal pending, pending_len, carry_only
        if not pending:
            return
        body = "\n".join(_block_text(block) for block in pending)
        # 只剩上片段的重复内容时不单独成片段，否则会出现"纯重叠"的碎片
        if carry_only and len(pending) == 1:
            pending, pending_len, carry_only = [], 0, False
            return
        heading = headings.path()
        content = _content_with_heading(body, heading)
        pages = [p for p in (_page_no(block) for block in pending) if p is not None]
        drafts.append(
            ChunkDraft(
                seq=len(drafts),
                content=content,
                heading_path=heading,
                block_ids=headings.block_ids() + [_block_id(block) for block in pending],
                first_page_no=min(pages) if pages else None,
                last_page_no=max(pages) if pages else None,
                char_count=len(content),
            )
        )
        if cfg.overlap > 0 and len(body) > cfg.overlap:
            tail = body[-cfg.overlap :]
            pending = [_TailBlock(pending[-1], tail)]
            pending_len = len(tail)
            carry_only = True
        else:
            pending, pending_len, carry_only = [], 0, False

    for block in blocks:
        if getattr(block, "is_discarded", False):
            continue
        text = _block_text(block)
        if not text:
            continue

        kind = _block_type(block)
        if kind == BlockType.TITLE.value:
            flush()
            headings.push(getattr(block, "level", None), text, _block_id(block))
            continue

        if kind == BlockType.TABLE.value:
            flush()
            _emit_single(drafts, headings, text, block)
            continue

        limit = _body_limit(cfg, headings.path())
        separator = 1 if pending else 0  # 拼接时补的换行也要算进预算
        if pending_len + separator + len(text) > limit:
            flush()
            limit = _body_limit(cfg, headings.path())
            separator = 1 if pending else 0
            if pending_len and pending_len + separator + len(text) > limit:
                # 重叠量已吃掉大部分预算时，别为凑窗口再产出一个纯重叠片段
                pending, pending_len, carry_only = [], 0, False
                separator = 0

        if len(text) > limit:
            pieces = split_long_text(text, limit)
            for piece in pieces[:-1]:
                _emit_single(drafts, headings, piece, block)
            tail = pieces[-1]
            pending = [_TailBlock(block, tail)]
            pending_len = len(tail)
            carry_only = False
            continue

        pending.append(block)
        pending_len += len(text)
        carry_only = False

    flush()
    return drafts


def _heading_section(blocks: list[Any], cfg: ChunkConfig) -> list[ChunkDraft]:
    headings = _HeadingStack()
    drafts: list[ChunkDraft] = []
    section: list[Any] = []

    def flush_section() -> None:
        nonlocal section
        if not section:
            return
        heading = headings.path()
        body = "\n".join(_block_text(block) for block in section)
        limit = _body_limit(cfg, heading)
        pages = [p for p in (_page_no(block) for block in section) if p is not None]
        # 未超限时 split_long_text 原样返回，正好一节一片段
        for piece in split_long_text(body, limit):
            content = _content_with_heading(piece, heading)
            drafts.append(
                ChunkDraft(
                    seq=len(drafts),
                    content=content,
                    heading_path=heading,
                    block_ids=headings.block_ids() + [_block_id(block) for block in section],
                    first_page_no=min(pages) if pages else None,
                    last_page_no=max(pages) if pages else None,
                    char_count=len(content),
                )
            )
        section = []

    for block in blocks:
        if getattr(block, "is_discarded", False):
            continue
        text = _block_text(block)
        if not text:
            continue

        kind = _block_type(block)
        if kind == BlockType.TITLE.value:
            flush_section()
            headings.push(getattr(block, "level", None), text, _block_id(block))
            continue

        if kind == BlockType.TABLE.value:
            flush_section()
            _emit_single(drafts, headings, text, block)
            continue

        section.append(block)

    flush_section()
    return drafts


_STRATEGIES: dict[str, Callable[[list[Any], ChunkConfig], list[ChunkDraft]]] = {
    BLOCK_MERGE: _block_merge,
    FIXED_WINDOW: _fixed_window,
    HEADING_SECTION: _heading_section,
}


class _TailBlock:
    """超长块切分后的尾段，只暴露切分逻辑需要的字段。"""

    __slots__ = ("block_type", "content", "id", "is_discarded", "level", "page_no")

    def __init__(self, origin: Any, text: str) -> None:
        self.block_type = _block_type(origin)
        self.content = text
        self.id = _block_id(origin)
        self.is_discarded = False
        self.level = getattr(origin, "level", None)
        self.page_no = _page_no(origin)


__all__ = ["ChunkDraft", "DEFAULT_CHUNK_HINT", "build_chunks", "split_long_text"]
