"""检索与问答的请求/响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DocTypeFilter = Literal["pdf", "office", "image", "text", "html"]


class RetrievalSource(BaseModel):
    """一条引用片段；``index`` 与答案里的 [1][2] 标注一一对应。"""

    index: int
    chunk_id: str
    document_id: str
    title: str
    version_no: int
    page_no: int | None = None
    page_range: list[int] | None = None
    heading_path: str | None = None
    score: float
    snippet: str


class RetrievalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500, description="检索词或问句")
    top_k: int | None = Field(default=None, ge=1, le=20, description="返回片段数上限")
    doc_type: DocTypeFilter | None = Field(default=None, description="按文档类型过滤")


class RetrievalSearchResponse(BaseModel):
    items: list[RetrievalSource]
    total: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500, description="问题")
    top_k: int | None = Field(default=None, ge=1, le=20)
    doc_type: DocTypeFilter | None = None
