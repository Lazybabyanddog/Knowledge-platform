"""HTML 引擎：按 DOM 结构把网页转成内容块。

与纯文本引擎的区别在于"按标签取语义"而不是按行猜测：
标题标签/标题类名映射为标题，段落标签映射为段落，表格整体保留行列。

策略可配置项：
- ``content_selector``：正文容器，默认整篇 ``body``
- ``title_selector``：额外的标题选择器
- ``merge_titles``：相邻标题合并（默认开启）
- ``drop_selectors``：追加剔除的节点
- ``keep_images``：是否产出图片块（默认关闭）

HTML 没有分页，所有块页码记为 1；正文为空时不伪造块，交由质量门禁标记低质量。
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from app.domain.enums import BlockType
from app.parsing.base import (
    EngineResult,
    ParsedBlock,
    ParseEngine,
    ParseError,
    ParseInput,
)
from app.parsing.registry import register_engine
from app.parsing.text_blocks import decode_text

HEADING_TAGS: dict[str, int] = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

# 非可见内容：直接跳过，不参与文本拼接
NOISE_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "iframe",
    "svg",
    "form",
    "input",
    "button",
    "link",
    "meta",
}
# 页面级噪声：保留为页眉页脚块，由 normalize 按 remove_header 丢弃
FRAME_TAGS = {
    "header": BlockType.HEADER,
    "footer": BlockType.FOOTER,
    "nav": BlockType.HEADER,
    "aside": BlockType.HEADER,
}

TEXT_TAGS = {"p", "dd", "dt", "figcaption", "caption", "blockquote"}
BLOCK_CHILD_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "blockquote",
    "table",
    "ul",
    "ol",
    "pre",
    "figure",
    "header",
    "footer",
    "nav",
    "aside",
    "dl",
    *HEADING_TAGS,
}

SENTENCE_END = ("。", "！", "？", "；", ".", "!", "?", ";")
WHITESPACE = re.compile(r"\s+")
TITLE_HINT = re.compile(r"title", re.IGNORECASE)
MAX_DEPTH = 60


def _clean(text: str) -> str:
    """折叠空白与不间断空格，保留单行。"""
    return WHITESPACE.sub(" ", text.replace("\xa0", " ")).strip()


def _text(node: Tag) -> str:
    return _clean(node.get_text(" ", strip=True))


def _own_text(node: Tag) -> str:
    """只取节点直系文本，避免与子块内容重复。"""
    parts = [str(child) for child in node.children if isinstance(child, NavigableString)]
    return _clean(" ".join(parts))


def _has_block_child(node: Tag) -> bool:
    return any(isinstance(child, Tag) and child.name in BLOCK_CHILD_TAGS for child in node.children)


def _looks_like_title(node: Tag) -> bool:
    """没有块级子节点、且类名或 id 带 title 的节点视为标题（公报类页面常见写法）。"""
    if _has_block_child(node):
        return False
    marker = " ".join([*(node.get("class") or []), str(node.get("id") or "")])
    return bool(TITLE_HINT.search(marker))


def _build_soup(text: str) -> tuple[BeautifulSoup, str]:
    """优先 lxml（容错好、快），缺失时回退标准库解析器。"""
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(text, parser), parser
        except Exception:  # noqa: BLE001 - 解析器缺失时换下一个
            continue
    raise ParseError("没有可用的 HTML 解析器（lxml / html.parser 均不可用）")


def _selector_error(selector: str, exc: Exception) -> ParseError:
    return ParseError(f"正文选择器非法: {selector}（{exc}）")


def _pick_container(
    soup: BeautifulSoup, config: dict[str, Any], warnings: list[str]
) -> Tag:
    selector = str(config.get("content_selector") or "body").strip() or "body"
    try:
        container = soup.select_one(selector)
    except Exception as exc:  # noqa: BLE001 - soupsieve 的语法错误类型随版本变化
        raise _selector_error(selector, exc) from exc
    if container is not None:
        return container

    warnings.append(f"正文选择器未命中: {selector}，已回退整篇 body")
    fallback = soup.body or soup
    return fallback if isinstance(fallback, Tag) else soup


def _collect_title_ids(container: Tag, config: dict[str, Any]) -> set[int]:
    selector = str(config.get("title_selector") or "").strip()
    if not selector:
        return set()
    try:
        nodes = container.select(selector)
    except Exception as exc:  # noqa: BLE001
        raise ParseError(f"标题选择器非法: {selector}（{exc}）") from exc
    return {id(node) for node in nodes}


def _drop_nodes(container: Tag, config: dict[str, Any], warnings: list[str]) -> None:
    selectors = config.get("drop_selectors") or []
    if isinstance(selectors, str):
        selectors = [selectors]
    for raw in selectors:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            for node in container.select(raw.strip()):
                node.decompose()
        except Exception:  # noqa: BLE001 - 站点配置写错不应中断整篇解析
            warnings.append(f"噪声选择器无效，已忽略: {raw}")


def _item_text(item: Tag) -> str:
    """列表项文本：跳过嵌套列表，避免子项内容被父项吞掉。"""
    parts: list[str] = []
    for child in item.children:
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag) and child.name not in {"ul", "ol"}:
            parts.append(child.get_text(" ", strip=True))
    return _clean(" ".join(parts))


def _table_block(table: Tag) -> ParsedBlock | None:
    """整表转成一个表格块；只取本表的行，嵌套表格的文本并入外层单元格。"""
    rows: list[list[str]] = []
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = [_text(cell) for cell in row.find_all(["td", "th"], recursive=False)]
        if cells:
            rows.append(cells)
    if not rows:
        return None
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    content = "\n".join("| " + " | ".join(row) + " |" for row in padded)
    return ParsedBlock(
        BlockType.TABLE,
        content,
        page_no=1,
        metadata={"columns": padded[0], "row_count": len(padded)},
    )


def _walk(node: Tag, blocks: list[ParsedBlock], *, title_ids: set[int], keep_images: bool,
          depth: int = 0) -> None:
    """深度优先遍历；命中块级规则即产出，不再深入该节点。"""
    if depth > MAX_DEPTH:
        text = _text(node)
        if text:
            blocks.append(ParsedBlock(BlockType.PARAGRAPH, text, page_no=1))
        return

    for child in node.children:
        if not isinstance(child, Tag):
            continue
        name = (child.name or "").lower()
        if name in NOISE_TAGS or child.has_attr("hidden"):
            continue

        if name in FRAME_TAGS:
            text = _text(child)
            if text:
                blocks.append(ParsedBlock(FRAME_TAGS[name], text, page_no=1))
            continue

        if name in {"ul", "ol"}:
            for item in child.find_all("li", recursive=False):
                text = _item_text(item)
                if text:
                    blocks.append(ParsedBlock(BlockType.LIST, text, page_no=1))
            continue

        if name == "table":
            block = _table_block(child)
            if block is not None:
                blocks.append(block)
            continue

        if name == "pre":
            code = child.get_text().strip("\n")
            if code.strip():
                blocks.append(
                    ParsedBlock(
                        BlockType.CODE,
                        code,
                        page_no=1,
                        metadata={"language": str(child.get("class") or ["unknown"])},
                    )
                )
            continue

        if id(child) in title_ids:
            text = _text(child)
            if text:
                blocks.append(
                    ParsedBlock(
                        BlockType.TITLE,
                        text,
                        page_no=1,
                        level=HEADING_TAGS.get(name, 1),
                    )
                )
                continue

        if name in HEADING_TAGS:
            text = _text(child)
            if text:
                blocks.append(
                    ParsedBlock(BlockType.TITLE, text, page_no=1, level=HEADING_TAGS[name])
                )
            continue

        if name == "img":
            if keep_images:
                alt = _clean(str(child.get("alt") or ""))
                blocks.append(
                    ParsedBlock(
                        BlockType.IMAGE,
                        alt,
                        page_no=1,
                        metadata={"alt": alt, "src": str(child.get("src") or "")},
                    )
                )
            continue

        if name == "code":
            code = child.get_text().strip("\n")
            if code.strip():
                blocks.append(
                    ParsedBlock(BlockType.CODE, code, page_no=1, metadata={"language": "unknown"})
                )
            continue

        if _looks_like_title(child):
            text = _text(child)
            if text:
                blocks.append(ParsedBlock(BlockType.TITLE, text, page_no=1, level=1))
            continue

        if name in TEXT_TAGS and not _has_block_child(child):
            text = _text(child)
            if text:
                blocks.append(ParsedBlock(BlockType.PARAGRAPH, text, page_no=1))
            continue

        if _has_block_child(child):
            _walk(child, blocks, title_ids=title_ids, keep_images=keep_images, depth=depth + 1)
            own = _own_text(child)
            if own:
                blocks.append(ParsedBlock(BlockType.PARAGRAPH, own, page_no=1))
            continue

        text = _text(child)
        if text:
            blocks.append(ParsedBlock(BlockType.PARAGRAPH, text, page_no=1))


def _merge_titles(blocks: list[ParsedBlock]) -> list[ParsedBlock]:
    """把被拆成多行的标题合并：前一段没有句末标点时继续拼接。"""
    merged: list[ParsedBlock] = []
    for block in blocks:
        previous = merged[-1] if merged else None
        if (
            block.block_type == BlockType.TITLE
            and previous is not None
            and previous.block_type == BlockType.TITLE
            and not previous.content.endswith(SENTENCE_END)
        ):
            previous.content = f"{previous.content} {block.content}".strip()
            previous.level = min(previous.level or 1, block.level or 1)
            continue
        merged.append(block)
    return merged


@register_engine
class HtmlDomEngine(ParseEngine):
    name = "html_dom"
    display_name = "HTML 结构解析"
    doc_types = ("html",)

    def parse(self, source: ParseInput) -> EngineResult:
        config = source.config or {}
        warnings: list[str] = []

        soup, parser = _build_soup(decode_text(source.data))
        container = _pick_container(soup, config, warnings)
        title_ids = _collect_title_ids(container, config)
        _drop_nodes(container, config, warnings)

        blocks: list[ParsedBlock] = []
        _walk(container, blocks, title_ids=title_ids, keep_images=bool(config.get("keep_images")))

        if bool(config.get("merge_titles", True)):
            blocks = _merge_titles(blocks)
        blocks = [
            block for block in blocks if block.content or block.block_type == BlockType.IMAGE
        ]

        if not any(block.block_type != BlockType.IMAGE for block in blocks):
            warnings.append("未从 HTML 中提取到有效正文")

        return EngineResult(
            blocks=blocks,
            warnings=warnings,
            stats={
                "page_count": 1,
                "char_count": sum(len(block.content) for block in blocks),
                "encoding": "html",
                "html_parser": parser,
                "body_selector": str(config.get("content_selector") or "body"),
            },
        )
