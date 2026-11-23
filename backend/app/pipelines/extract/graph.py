"""LangGraph 抽取图装配。

    chunk_blocks → classify_blocks → extract_entities → extract_relations → schema_validate
                                                    ↖ repair_output ←┘（最多 N 次）
    schema_validate → commit_batch / skip_batch → （下一批 → extract_entities）→ dedupe → persist
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.pipelines.extract.nodes import (
    chunk_blocks,
    classify_blocks,
    commit_batch,
    dedupe_candidates,
    extract_entities,
    extract_relations,
    persist_candidates,
    repair_output,
    route_after_batch,
    route_after_chunk,
    route_after_classify,
    route_after_validate,
    schema_validate,
    skip_batch,
)
from app.pipelines.extract.state import ExtractState


@lru_cache
def get_extract_graph() -> Any:
    graph = StateGraph(ExtractState)
    graph.add_node("chunk_blocks", chunk_blocks)
    graph.add_node("classify_blocks", classify_blocks)
    graph.add_node("extract_entities", extract_entities)
    graph.add_node("extract_relations", extract_relations)
    graph.add_node("schema_validate", schema_validate)
    graph.add_node("repair_output", repair_output)
    graph.add_node("commit_batch", commit_batch)
    graph.add_node("skip_batch", skip_batch)
    graph.add_node("dedupe_candidates", dedupe_candidates)
    graph.add_node("persist_candidates", persist_candidates)

    graph.add_edge(START, "chunk_blocks")
    # 断点恢复到末尾（或全部批次已完成）时不再跑空批，直接去去重与落库
    graph.add_conditional_edges(
        "chunk_blocks",
        route_after_chunk,
        {"extract": "classify_blocks", "done": "dedupe_candidates"},
    )
    graph.add_conditional_edges(
        "classify_blocks",
        route_after_classify,
        {"extract": "extract_entities", "skip": "skip_batch"},
    )
    graph.add_edge("extract_entities", "extract_relations")
    graph.add_edge("extract_relations", "schema_validate")

    graph.add_conditional_edges(
        "schema_validate",
        route_after_validate,
        {
            "repair": "repair_output",
            "commit": "commit_batch",
            "skip": "skip_batch",
        },
    )
    graph.add_edge("repair_output", "schema_validate")

    for node in ("commit_batch", "skip_batch"):
        graph.add_conditional_edges(
            node,
            route_after_batch,
            {"next": "classify_blocks", "done": "dedupe_candidates"},
        )

    graph.add_edge("dedupe_candidates", "persist_candidates")
    graph.add_edge("persist_candidates", END)
    return graph.compile()


def run_extract_pipeline(state: dict[str, Any]) -> dict[str, Any]:
    return get_extract_graph().invoke(state)


__all__ = ["get_extract_graph", "run_extract_pipeline"]
