"""解析图装配：把引擎、表格规整、归一化与质量门禁串成一条可测试的流水线。"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.pipelines.parse import nodes
from app.pipelines.parse.routers import route_by_doc_type, route_needs_ocr, route_quality
from app.pipelines.state import ParseState

ROUTE_TARGETS = (
    "layout_parse",
    "plain_parse",
    "office_parse",
    "ocr_parse",
    "html_parse",
)


def build_parse_graph():
    graph = StateGraph(ParseState)

    graph.add_node("detect_type", nodes.detect_type)
    graph.add_node("layout_parse", nodes.make_engine_node("pdf_layout"))
    graph.add_node("plain_parse", nodes.make_engine_node("plain_text"))
    graph.add_node("office_parse", nodes.make_engine_node("office"))
    graph.add_node("ocr_parse", nodes.make_engine_node("ocr"))
    # HTML 分支的兜底引擎也是 html_dom：策略误配 plain_text 时不会退回按行切源码
    graph.add_node("html_parse", nodes.make_engine_node("html_dom"))
    graph.add_node("table_extract", nodes.table_extract)
    graph.add_node("normalize", nodes.normalize)
    graph.add_node("quality_score", nodes.quality_score)
    graph.add_node("persist_blocks", nodes.persist_blocks)
    graph.add_node("mark_low_quality", nodes.mark_low_quality)

    graph.add_edge(START, "detect_type")
    graph.add_conditional_edges(
        "detect_type",
        route_by_doc_type,
        {route: route for route in ROUTE_TARGETS},
    )
    graph.add_conditional_edges(
        "layout_parse",
        route_needs_ocr,
        {"ocr_parse": "ocr_parse", "table_extract": "table_extract"},
    )
    graph.add_edge("ocr_parse", "table_extract")
    graph.add_edge("plain_parse", "table_extract")
    graph.add_edge("office_parse", "table_extract")
    graph.add_edge("html_parse", "table_extract")
    graph.add_edge("table_extract", "normalize")
    graph.add_edge("normalize", "quality_score")
    graph.add_conditional_edges(
        "quality_score",
        route_quality,
        {"mark_low_quality": "mark_low_quality", "persist_blocks": "persist_blocks"},
    )
    graph.add_edge("persist_blocks", END)
    graph.add_edge("mark_low_quality", END)

    return graph.compile()


@lru_cache(maxsize=1)
def get_parse_graph():
    """图本身无状态，编译一次复用；跨进程的续跑靠 Celery 重试而非内存 checkpoint。"""
    return build_parse_graph()


def run_parse_pipeline(
    *,
    data: bytes,
    file_name: str,
    mime_type: str,
    config: dict[str, Any] | None = None,
    engine: str = "",
    doc_type: str | None = None,
    task_id: str = "",
) -> ParseState:
    state: ParseState = {
        "task_id": task_id,
        "data": data,
        "file_name": file_name,
        "mime_type": mime_type,
        "config": config or {},
        "engine": engine,
        "blocks": [],
        "warnings": [],
        "stats": {},
    }
    if doc_type:
        state["doc_type"] = doc_type
    return get_parse_graph().invoke(state)
