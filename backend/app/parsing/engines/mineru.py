"""MinerU 引擎：调用本机部署的 MinerU 服务，把版面产物映射为内容块。

形态选择见 ``design.md`` D1：进程内 SDK 会把模型权重与 GPU 依赖拖进 parse worker，
HTTP 形态则让"服务挂了"变成一个可重试、可诊断的错误。

端点与字段随 MinerU 版本漂移，因此调用与解析收敛在 ``_request``/``_map`` 两处，
升级 MinerU 只需要改这两个方法。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.config import settings
from app.domain.enums import BlockType
from app.parsing.base import (
    EngineResult,
    MineruUnavailableError,
    ParsedBlock,
    ParseEngine,
    ParseError,
    ParseInput,
)
from app.parsing.registry import register_engine

PARSE_PATH = "/file_parse"
_TITLE_TYPES = {"title", "doc_title", "paragraph_title"}
_LIST_TYPES = {"list"}
_TABLE_TYPES = {"table"}
_IMAGE_TYPES = {"image", "figure"}
_FORMULA_TYPES = {"equation", "interline_equation", "inline_equation"}
# 这些键在不同版本里出现过，按顺序取第一个能用的
_RESULT_KEYS = ("results", "data")


def _flag(value: Any) -> str:
    return "true" if value else "false"


def _html_table_to_markdown(html: str) -> str:
    """MinerU 的表格是 HTML；平台其余环节按 markdown 表格处理，这里统一。"""
    text = html.strip()
    if not text:
        return ""
    if "<" not in text:
        return text

    soup = BeautifulSoup(text, "lxml")
    rows: list[list[str]] = []
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(row) + " |" for row in padded]
    if len(lines) >= 2:
        lines.insert(1, "| " + " | ".join(["---"] * width) + " |")
    return "\n".join(lines)


@register_engine
class MineruEngine(ParseEngine):
    name = "mineru"
    display_name = "MinerU（版面解析）"
    doc_types = ("pdf",)

    def config_problem(self) -> str | None:
        if not settings.mineru_base_url.strip():
            return (
                "MinerU 服务地址未配置（MINERU_BASE_URL），"
                "请先在本机启动 MinerU 服务并填写地址"
            )
        return None

    def parse(self, source: ParseInput) -> EngineResult:
        problem = self.config_problem()
        if problem:
            raise MineruUnavailableError(problem)
        payload = self._request(source)
        return self._map(payload)

    # ------------------------------------------------------------------ #
    # 调用
    # ------------------------------------------------------------------ #
    def _request(self, source: ParseInput) -> Any:
        config = source.config or {}
        form = {
            "backend": str(config.get("mineru_backend") or settings.mineru_backend or "pipeline"),
            "lang_list": str(config.get("mineru_lang") or settings.mineru_lang or "ch"),
            "return_content_list": "true",
            "return_md": "true",
            "table_enable": _flag(config.get("mineru_table_enable", True)),
            "formula_enable": _flag(config.get("mineru_formula_enable", True)),
        }
        files = {
            "files": (
                source.file_name or "document.pdf",
                source.data,
                source.mime_type or "application/pdf",
            )
        }
        timeout = httpx.Timeout(settings.mineru_timeout_seconds, connect=10.0)
        url = settings.mineru_base_url.rstrip("/") + PARSE_PATH

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, files=files, data=form)
                response.raise_for_status()
                return response.json()
        except httpx.TimeoutException as exc:
            raise MineruUnavailableError(
                f"MinerU 调用超时（{settings.mineru_timeout_seconds}s）"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            # 5xx 与 429 是服务端/sidecar 的问题，重跑有意义；4xx 是文件不被接受
            if status >= 500 or status == 429:
                raise MineruUnavailableError(f"MinerU 服务返回 {status}") from exc
            raise ParseError(f"MinerU 拒绝该文件（HTTP {status}）") from exc
        except httpx.TransportError as exc:
            raise MineruUnavailableError(f"无法连接 MinerU 服务: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ParseError(f"MinerU 响应不是合法 JSON: {exc}") from exc

    # ------------------------------------------------------------------ #
    # 产物映射
    # ------------------------------------------------------------------ #
    def _result_of(self, payload: Any) -> dict[str, Any]:
        """从不同版本的响应外壳里取出真正含 content_list 的那一层。"""
        if not isinstance(payload, dict):
            raise ParseError("MinerU 响应格式不合法")

        for key in _RESULT_KEYS:
            inner = payload.get(key)
            if isinstance(inner, dict) and "content_list" in inner:
                return inner
            # results 里通常是 {文件名: 结果}
            if isinstance(inner, dict):
                for value in inner.values():
                    if isinstance(value, dict) and "content_list" in value:
                        return value

        if "content_list" in payload:
            return payload

        for value in payload.values():
            if isinstance(value, dict) and "content_list" in value:
                return value

        raise ParseError("MinerU 响应缺少内容列表")

    def _map(self, payload: Any) -> EngineResult:
        result = self._result_of(payload)
        content_list = result.get("content_list")
        if not isinstance(content_list, list):
            raise ParseError("MinerU 内容列表格式不合法")

        blocks: list[ParsedBlock] = []
        for item in content_list:
            if not isinstance(item, dict):
                continue
            block = self._block_of(item)
            if block is not None:
                blocks.append(block)

        warnings: list[str] = []
        failed_pages = self._failed_pages(result)
        if failed_pages:
            warnings.append(
                "MinerU 在以下页返回失败，内容可能缺失: "
                + ", ".join(str(page) for page in failed_pages)
            )
        if not blocks:
            raise ParseError("MinerU 未返回可用的内容块")

        for index, block in enumerate(blocks):
            block.seq = index

        pages = {block.page_no for block in blocks if block.page_no is not None}
        return EngineResult(
            blocks=blocks,
            warnings=warnings,
            stats={
                "mineru_backend": result.get("backend") or settings.mineru_backend,
                "page_count": max(pages) if pages else None,
                "block_count": len(blocks),
                "failed_pages": len(failed_pages),
            },
        )

    def _block_of(self, item: dict[str, Any]) -> ParsedBlock | None:
        kind = str(item.get("type") or "").strip().lower()
        text = str(item.get("text") or "").strip()
        page_no = self._page_no(item)
        bbox = self._bbox(item)

        if kind in _TITLE_TYPES:
            if not text:
                return None
            level = item.get("text_level")
            depth = level if isinstance(level, int) and level > 0 else 1
            return ParsedBlock(
                BlockType.TITLE, text, page_no=page_no, bbox=bbox, level=depth
            )

        if kind in _TABLE_TYPES:
            markdown = _html_table_to_markdown(
                str(item.get("table_body") or item.get("table_html") or text)
            )
            if not markdown:
                return None
            return ParsedBlock(
                BlockType.TABLE,
                markdown,
                page_no=page_no,
                bbox=bbox,
                metadata={"source": "mineru"},
            )

        if kind in _IMAGE_TYPES:
            resource = str(item.get("img_path") or item.get("image_path") or "").strip()
            metadata: dict[str, Any] = {"source": "mineru"}
            if resource:
                metadata["resource"] = resource
            # 图片不落对象存储，只登记来源；content 留空，不参与切块
            return ParsedBlock(BlockType.IMAGE, "", page_no=page_no, bbox=bbox, metadata=metadata)

        if kind in _FORMULA_TYPES:
            latex = str(item.get("latex") or text or "").strip()
            if not latex:
                return None
            # 不新增 BlockType：公式以段落承载，元数据标记，切块与检索都能带上它
            return ParsedBlock(
                BlockType.PARAGRAPH,
                latex,
                page_no=page_no,
                bbox=bbox,
                metadata={"kind": "formula", "source": "mineru"},
            )

        if kind in _LIST_TYPES:
            if not text:
                return None
            return ParsedBlock(
                BlockType.LIST,
                text,
                page_no=page_no,
                bbox=bbox,
                metadata={"source": "mineru"},
            )

        if not text:
            return None
        return ParsedBlock(
            BlockType.PARAGRAPH, text, page_no=page_no, bbox=bbox, metadata={"source": "mineru"}
        )

    @staticmethod
    def _page_no(item: dict[str, Any]) -> int | None:
        raw = item.get("page_idx", item.get("page_no"))
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            # MinerU 的 page_idx 从 0 开始，平台页码从 1 开始
            return raw + 1
        return None

    @staticmethod
    def _bbox(item: dict[str, Any]) -> dict[str, float] | None:
        raw = item.get("bbox")
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        try:
            x0, y0, x1, y1 = (float(value) for value in raw)
        except (TypeError, ValueError):
            return None
        return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}

    @staticmethod
    def _failed_pages(result: dict[str, Any]) -> list[int]:
        raw = result.get("failed_pages") or result.get("fail_pages") or []
        if not isinstance(raw, list):
            return []
        pages: list[int] = []
        for value in raw:
            if isinstance(value, int) and not isinstance(value, bool):
                pages.append(value)
        return pages
