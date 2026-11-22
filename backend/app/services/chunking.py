"""把解析块合并成可检索片段。

规则（对应 ``specs/knowledge-retrieval`` 的切分需求）：

- 相邻的正文类块合并，累计长度受 ``chunk_hint`` 约束；
- 标题块不单独成片段，作为其后片段的上下文前缀（标题栈）；
- 表格块整体保留，独立成片段；
- 被标记丢弃的块与空块不参与合并；
- 单块超长时按「空行 → 句末 → 硬切」三级回退，尽量避免截断到半句。

本模块是纯函数，不碰数据库，便于单测。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import BlockType

DEFAULT_CHUNK_HINT = 800
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


def build_chunks(blocks: Iterable[Any], chunk_hint: int = DEFAULT_CHUNK_HINT) -> list[ChunkDraft]:
    """把按 ``seq`` 排好序的块合并成片段列表。"""
    hint = chunk_hint if chunk_hint and chunk_hint > 0 else DEFAULT_CHUNK_HINT
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
        content = f"{heading}\n{body}" if heading else body
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

    def emit(text: str, block: Any, heading: str | None) -> None:
        content = f"{heading}\n{text}" if heading else text
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
            emit(text, block, headings.path())
            continue

        if pending_len + len(text) > hint:
            flush()

        if len(text) > hint:
            # 单块超长：整段切分，前面的部分独立成片段，末尾继续参与累积
            pieces = split_long_text(text, hint)
            for piece in pieces[:-1]:
                emit(piece, block, headings.path())
            tail = pieces[-1]
            pending.append(_TailBlock(block, tail))
            pending_len = len(tail)
            continue

        pending.append(block)
        pending_len += len(text)

    flush()
    return drafts


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
