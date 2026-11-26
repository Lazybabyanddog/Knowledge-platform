"""切块策略：配置校验与 fixed_window / heading_section 的切分行为。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.domain.enums import BlockType
from app.services.chunk_config import (
    BLOCK_MERGE,
    FIXED_WINDOW,
    HEADING_SECTION,
    ChunkConfig,
    ChunkConfigError,
    parse_chunk_config,
)
from app.services.chunking import build_chunks


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


def _table(seq: int, text: str = "| a | b |\n| --- | --- |\n| 1 | 2 |") -> Block:
    return Block(seq=seq, block_type=BlockType.TABLE, content=text)


def _sentences(count: int) -> str:
    return "".join(f"第{index:02d}句的内容写在这里。" for index in range(count))


# --------------------------------------------------------------------------- #
# 配置解析
# --------------------------------------------------------------------------- #
def test_missing_chunk_block_keeps_legacy_behaviour() -> None:
    cfg = parse_chunk_config({"chunk_hint": 600})
    assert cfg.strategy == BLOCK_MERGE
    assert cfg.size == 600
    assert cfg.overlap == 0


def test_chunk_size_wins_over_hint() -> None:
    cfg = parse_chunk_config({"chunk": {"size": 500}, "chunk_hint": 600})
    assert cfg.size == 500


def test_defaults_when_nothing_configured() -> None:
    cfg = parse_chunk_config(None)
    assert cfg == ChunkConfig()


def test_rejects_unknown_strategy() -> None:
    with pytest.raises(ChunkConfigError) as excinfo:
        parse_chunk_config({"chunk": {"strategy": "semantic"}})
    assert "semantic" in str(excinfo.value)


@pytest.mark.parametrize("size", [99, 4001])
def test_rejects_out_of_range_size(size: int) -> None:
    with pytest.raises(ChunkConfigError):
        parse_chunk_config({"chunk": {"strategy": FIXED_WINDOW, "size": size}})


def test_rejects_overlap_on_non_window_strategy() -> None:
    with pytest.raises(ChunkConfigError) as excinfo:
        parse_chunk_config({"chunk": {"strategy": BLOCK_MERGE, "overlap": 100}})
    assert "fixed_window" in str(excinfo.value)


def test_rejects_overlap_larger_than_half_size() -> None:
    with pytest.raises(ChunkConfigError):
        parse_chunk_config({"chunk": {"strategy": FIXED_WINDOW, "size": 200, "overlap": 120}})


def test_rejects_non_object_chunk() -> None:
    with pytest.raises(ChunkConfigError):
        parse_chunk_config({"chunk": "fixed_window"})


# --------------------------------------------------------------------------- #
# fixed_window
# --------------------------------------------------------------------------- #
def test_fixed_window_keeps_chunks_within_size() -> None:
    blocks = [_para(index, _sentences(6)) for index in range(4)]
    cfg = ChunkConfig(strategy=FIXED_WINDOW, size=200, overlap=0)

    chunks = build_chunks(blocks, cfg)

    assert len(chunks) > 1
    assert all(chunk.char_count <= 200 for chunk in chunks)
    # 以句末收尾，不出现半句
    assert all(chunk.content.endswith("。") for chunk in chunks)


def test_fixed_window_overlaps_neighbours() -> None:
    blocks = [_para(0, _sentences(8)), _para(1, _sentences(8)), _para(2, _sentences(8))]
    cfg = ChunkConfig(strategy=FIXED_WINDOW, size=150, overlap=40)

    chunks = build_chunks(blocks, cfg)

    assert len(chunks) > 1
    tail = chunks[0].content[-40:]
    assert chunks[1].content.startswith(tail)


def test_fixed_window_counts_heading_prefix_in_size() -> None:
    heading = "第三章 预算管理"
    blocks = [_title(0, heading), _para(1, _sentences(10))]
    cfg = ChunkConfig(strategy=FIXED_WINDOW, size=200, overlap=0)

    chunks = build_chunks(blocks, cfg)

    assert all(chunk.char_count <= 200 for chunk in chunks)
    assert all(chunk.content.startswith(f"{heading}\n") for chunk in chunks)


def test_fixed_window_keeps_table_standalone() -> None:
    blocks = [_para(0, _sentences(4)), _table(1), _para(2, _sentences(4))]
    cfg = ChunkConfig(strategy=FIXED_WINDOW, size=300, overlap=0)

    chunks = build_chunks(blocks, cfg)

    table_chunks = [chunk for chunk in chunks if chunk.content.startswith("| a |")]
    assert len(table_chunks) == 1
    assert table_chunks[0].block_ids[-1] == "block-1"


# --------------------------------------------------------------------------- #
# heading_section
# --------------------------------------------------------------------------- #
def test_heading_section_splits_by_heading() -> None:
    blocks = [
        _title(0, "第一章 总则"),
        _para(1, _sentences(4)),
        _title(2, "第二章 预算"),
        _para(3, _sentences(4)),
        _title(4, "第三章 归档"),
        _para(5, _sentences(4)),
    ]
    cfg = ChunkConfig(strategy=HEADING_SECTION, size=800)

    chunks = build_chunks(blocks, cfg)

    assert [chunk.heading_path for chunk in chunks] == [
        "第一章 总则",
        "第二章 预算",
        "第三章 归档",
    ]
    assert all(chunk.content.startswith(f"{chunk.heading_path}\n") for chunk in chunks)


def test_heading_section_splits_oversized_section() -> None:
    heading = "第二章 预算"
    blocks = [_title(0, heading), _para(1, _sentences(60))]
    cfg = ChunkConfig(strategy=HEADING_SECTION, size=200)

    chunks = build_chunks(blocks, cfg)

    assert len(chunks) > 1
    assert all(chunk.content.startswith(f"{heading}\n") for chunk in chunks)
    assert all(chunk.char_count <= 200 for chunk in chunks)


def test_heading_section_does_not_merge_across_headings() -> None:
    blocks = [
        _title(0, "第一节 A"),
        _para(1, "A 节内容。"),
        _title(2, "第二节 B"),
        _para(3, "B 节内容。"),
    ]
    cfg = ChunkConfig(strategy=HEADING_SECTION, size=800)

    chunks = build_chunks(blocks, cfg)

    assert len(chunks) == 2
    assert "A 节内容" in chunks[0].content
    assert "B 节内容" in chunks[1].content
    assert "A 节内容" not in chunks[1].content


def test_heading_section_keeps_table_standalone() -> None:
    blocks = [_title(0, "第一章"), _para(1, _sentences(3)), _table(2), _para(3, _sentences(3))]
    cfg = ChunkConfig(strategy=HEADING_SECTION, size=800)

    chunks = build_chunks(blocks, cfg)

    table_chunks = [chunk for chunk in chunks if "| 1 | 2 |" in chunk.content]
    assert len(table_chunks) == 1
    # 表格前后正文仍属于同一节，但不会被塞进表格片段
    assert "第00句" not in table_chunks[0].content
    body = "\n".join(chunk.content for chunk in chunks)
    assert "第00句" in body
