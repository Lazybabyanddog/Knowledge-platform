"""内置引擎集合：导入即完成注册，新增引擎时在这里追加一行。"""

from app.parsing.engines import (  # noqa: F401
    court_guide_case,
    html_dom,
    mineru,
    ocr,
    office,
    pdf_layout,
    plain_text,
)

__all__ = [
    "court_guide_case",
    "html_dom",
    "mineru",
    "ocr",
    "office",
    "pdf_layout",
    "plain_text",
]
