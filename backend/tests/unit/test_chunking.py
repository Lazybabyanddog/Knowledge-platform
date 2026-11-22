"""片段切分规则的单测：不连数据库，只验证合并与切分行为。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import BlockType
from app.services.chunking import build_chunks, split_long_text


@dataclass
class Block:
    seq: int
    block_type: BlockType
    content: str
    level: int | None = None
    page_no: int | None = 1
    is_discarded: bool = False
    id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"block-{self.seq}"


def _title(seq: int, text: str, level: int = 2) -> Block:
    return Block(seq=seq, block_type=BlockType.TITLE, content=text, level=level)


def _para(seq: int, text: str, page_no: int = 1) -> Block:
    return Block(seq=seq, block_type=BlockType.PARAGRAPH, content=text, page_no=page_no)


def test_heading_becomes_prefix_of_following_paragraphs() -> None:
    blocks = [
        _title(0, "第三章 预算"),
        _para(1, "本章规定预算编制流程。" * 2),
        _para(2, "预算一经批准不得随意调整。" * 2, page_no=3),
    ]

    chunks = build_chunks(blocks, chunk_hint=800)

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.heading_path == "第三章 预算"
    assert chunk.content.startswith("第三章 预算\n")
    assert chunk.block_ids == ["block-0", "block-1", "block-2"]
    assert (chunk.first_page_no, chunk.last_page_no) == (1, 3)
    assert chunk.char_count == len(chunk.content)


def test_title_is_not_a_standalone_chunk() -> None:
    blocks = [
        _title(0, "一、总则"),
        _title(1, "（一）适用范围", level=3),
        _para(2, "本办法适用于全部单位。"),
    ]

    chunks = build_chunks(blocks, chunk_hint=800)

    assert len(chunks) == 1
    assert chunks[0].heading_path == "一、总则 / （一）适用范围"


def test_table_is_isolated_from_neighbouring_paragraphs() -> None:
    blocks = [
        _para(0, "下表列出各项支出。"),
        Block(seq=1, block_type=BlockType.TABLE, content="| 项目 | 金额 |\n| 办公 | 100 |"),
        _para(2, "以上金额以万元计。"),
    ]

    chunks = build_chunks(blocks, chunk_hint=800)

    assert len(chunks) == 3
    assert chunks[1].block_ids == ["block-1"]
    assert chunks[1].content == "| 项目 | 金额 |\n| 办公 | 100 |"
    assert all(chunk.block_ids != ["block-1", "block-2"] for chunk in chunks)


def test_discarded_blocks_are_excluded() -> None:
    discarded = Block(
        seq=1, block_type=BlockType.PARAGRAPH, content="这段已被人工丢弃。", is_discarded=True
    )
    blocks = [_para(0, "保留的第一段。"), discarded, _para(2, "保留的第二段。")]

    chunks = build_chunks(blocks, chunk_hint=800)

    assert len(chunks) == 1
    assert chunks[0].block_ids == ["block-0", "block-2"]
    assert "丢弃" not in chunks[0].content


def test_oversized_block_is_split_without_breaking_sentences() -> None:
    sentence = "本条规定了预算调整的具体流程与审批权限。"
    text = sentence * 40
    blocks = [_para(0, text)]

    chunks = build_chunks(blocks, chunk_hint=200)

    assert len(chunks) > 1
    assert all(chunk.char_count <= 200 + len(sentence) for chunk in chunks)
    # 除末段外都应在句末断开，拼接后可还原原文
    assert all(chunk.content.endswith("。") for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == text


def test_split_long_text_packs_sentences_up_to_limit() -> None:
    text = "甲。" * 100

    pieces = split_long_text(text, 30)

    assert all(len(piece) <= 30 for piece in pieces)
    assert "".join(pieces) == text
