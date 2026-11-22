"""内置引擎集合：导入即完成注册，新增引擎时在这里追加一行。"""

from app.parsing.engines import html_dom, ocr, office, pdf_layout, plain_text  # noqa: F401

__all__ = ["html_dom", "ocr", "office", "pdf_layout", "plain_text"]
