"""测试用 PDF 生成器：构造带文字层与"纯图片"两类 PDF。"""

from __future__ import annotations

from io import BytesIO

from reportlab.pdfgen import canvas

DEFAULT_PAGES = [
    ["Annual Report 2024", "Revenue grew steadily across all regions."],
    ["Chapter 2 Operations", "The operations team delivered three releases."],
    ["Chapter 3 Outlook", "We will keep investing in platform reliability."],
]


def make_text_pdf(pages: list[list[str]] | None = None) -> bytes:
    """每页写入若干行文本，形成真实的文字层。"""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    for lines in pages or DEFAULT_PAGES:
        y = 780
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 24
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def make_scanned_pdf(page_count: int = 2) -> bytes:
    """只有图形、没有文字层，模拟扫描件。"""
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    for _ in range(page_count):
        pdf.rect(100, 400, 300, 200, stroke=1, fill=0)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()
