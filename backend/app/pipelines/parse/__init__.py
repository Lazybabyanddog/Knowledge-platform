"""解析流水线：detect_type → 引擎解析 → 表格规整 → 归一化 → 质量评分。"""

from app.pipelines.parse.graph import build_parse_graph, get_parse_graph, run_parse_pipeline

__all__ = ["build_parse_graph", "get_parse_graph", "run_parse_pipeline"]
