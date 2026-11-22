"""Office 引擎：直接解包 OOXML，不依赖 LibreOffice 或 python-docx。

docx / xlsx / pptx 本质是 zip + XML，按需读取对应 part 即可；
老式二进制格式（doc/xls/ppt）明确报错，提示另存为新格式，避免产出乱码。
"""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree

from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParsedBlock, ParseEngine, ParseError, ParseInput
from app.parsing.registry import register_engine

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

SLIDE_NAME = re.compile(r"ppt/slides/slide(\d+)\.xml$")


def _read_zip(data: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ParseError("Office 文件已损坏或不是合法的 OOXML 包") from exc


def _parse_docx(archive: zipfile.ZipFile) -> list[ParsedBlock]:
    try:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    except KeyError as exc:
        raise ParseError("docx 缺少 word/document.xml") from exc

    blocks: list[ParsedBlock] = []
    for paragraph in root.iter(f"{W}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{W}t")).strip()
        if not text:
            continue
        style = paragraph.find(f"{W}pPr/{W}pStyle")
        style_value = (style.get(f"{W}val") if style is not None else "") or ""
        if style_value.lower().startswith("heading"):
            level = "".join(ch for ch in style_value if ch.isdigit())
            blocks.append(
                ParsedBlock(
                    BlockType.TITLE,
                    text,
                    level=int(level) if level else 1,
                    seq=len(blocks),
                )
            )
        else:
            blocks.append(ParsedBlock(BlockType.PARAGRAPH, text, seq=len(blocks)))

    if not blocks:
        raise ParseError("docx 中未找到可提取的文字")
    return blocks


def _parse_xlsx(archive: zipfile.ZipFile) -> list[ParsedBlock]:
    shared: list[str] = []
    try:
        shared_root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in shared_root.iter(f"{S}si"):
            shared.append("".join(node.text or "" for node in item.iter(f"{S}t")))
    except KeyError:
        pass

    try:
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    except KeyError as exc:
        raise ParseError("xlsx 缺少工作表数据") from exc

    rows: list[str] = []
    for row in sheet.iter(f"{S}row"):
        cells: list[str] = []
        for cell in row.iter(f"{S}c"):
            cell_type = cell.get("t")
            value_node = cell.find(f"{S}v")
            if cell_type == "inlineStr":
                text = "".join(node.text or "" for node in cell.iter(f"{S}t"))
            elif value_node is None or value_node.text is None:
                text = ""
            elif cell_type == "s":
                index = int(value_node.text)
                text = shared[index] if index < len(shared) else ""
            else:
                text = value_node.text
            cells.append(text.strip())
        if any(cells):
            rows.append("| " + " | ".join(cells) + " |")

    if not rows:
        raise ParseError("xlsx 中未找到可提取的数据")
    return [
        ParsedBlock(
            BlockType.TABLE,
            "\n".join(rows),
            metadata={"row_count": len(rows), "sheet": "sheet1"},
            seq=0,
        )
    ]


def _parse_pptx(archive: zipfile.ZipFile) -> list[ParsedBlock]:
    slides = sorted(
        (name for name in archive.namelist() if SLIDE_NAME.match(name)),
        key=lambda name: int(SLIDE_NAME.match(name).group(1)),  # type: ignore[union-attr]
    )
    if not slides:
        raise ParseError("pptx 中未找到幻灯片")

    blocks: list[ParsedBlock] = []
    for page_no, name in enumerate(slides, start=1):
        root = ElementTree.fromstring(archive.read(name))
        first = True
        for paragraph in root.iter(f"{A}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{A}t")).strip()
            if not text:
                continue
            block_type = BlockType.TITLE if first else BlockType.PARAGRAPH
            blocks.append(
                ParsedBlock(
                    block_type,
                    text,
                    page_no=page_no,
                    level=1 if first else None,
                    metadata={"slide": page_no},
                    seq=len(blocks),
                )
            )
            first = False
    return blocks


@register_engine
class OfficeEngine(ParseEngine):
    name = "office"
    display_name = "Office 文档"
    doc_types = ("office",)

    def parse(self, source: ParseInput) -> EngineResult:
        suffix = source.file_name.rsplit(".", 1)[-1].lower() if "." in source.file_name else ""
        if suffix in {"doc", "xls", "ppt"}:
            raise ParseError(f"暂不支持 .{suffix} 老格式，请另存为 .{suffix}x 后重新上传")

        archive = _read_zip(source.data)
        with archive:
            if suffix == "docx":
                blocks = _parse_docx(archive)
            elif suffix == "xlsx":
                blocks = _parse_xlsx(archive)
            elif suffix == "pptx":
                blocks = _parse_pptx(archive)
            else:
                raise ParseError(f"Office 引擎不支持的文件类型: .{suffix}")

        page_count = max((block.page_no or 1) for block in blocks)
        return EngineResult(
            blocks=blocks,
            stats={
                "page_count": page_count,
                "char_count": sum(len(block.content) for block in blocks),
            },
        )
