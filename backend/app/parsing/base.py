"""解析引擎的公共契约。

引擎只做"字节 -> 内容块"的纯转换，不碰数据库、不碰队列，便于单测与替换实现。
新增引擎只需继承 ``ParseEngine`` 并用 ``register_engine`` 注册，策略即可引用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import BlockType


class ParseError(Exception):
    """引擎内部失败：worker 捕获后把任务置为失败并记录原因。"""

    # 对外错误码：非重试类，重试只会重复同样的失败
    task_error_code = "parse_error"


class UnknownEngineError(ParseError):
    """策略引用了未注册的引擎，触发解析前就应拦截。"""


class MineruUnavailableError(ParseError):
    """MinerU 服务不可达、超时或返回 5xx：是服务故障，值得按重试预算重跑。

    与 ``ParseError`` 的区别只在对外错误码：前者不可重试，这个可重试，
    运维据此判断"去修服务"还是"去修文档"。
    """

    task_error_code = "mineru_unavailable"


@dataclass(slots=True)
class ParsedBlock:
    """引擎产出的单个内容块；seq 与 parent_index 在 normalize 节点统一分配。"""

    block_type: BlockType
    content: str
    page_no: int | None = None
    bbox: dict[str, float] | None = None
    level: int | None = None
    parent_index: int | None = None
    is_discarded: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    seq: int = 0


@dataclass(slots=True)
class ParseInput:
    data: bytes
    file_name: str
    mime_type: str
    doc_type: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EngineResult:
    blocks: list[ParsedBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    # 版面解析发现"没有文字层"，由路由器决定是否改走 OCR
    needs_ocr: bool = False


class ParseEngine(ABC):
    """解析引擎基类：``name`` 是策略里的 engine 取值。"""

    name: str
    display_name: str = ""
    doc_types: tuple[str, ...] = ()

    def supports(self, doc_type: str) -> bool:
        return doc_type in self.doc_types or not self.doc_types

    def config_problem(self) -> str | None:
        """引擎当前不可用的原因；``None`` 表示可用。

        保存策略时用它提前拦截（例如 MinerU 服务地址未配置），
        避免把"注定失败"的配置存下来，等到 worker 里才报错。
        """
        return None

    @abstractmethod
    def parse(self, source: ParseInput) -> EngineResult:
        """把文件内容转换成有序内容块；失败请抛 ``ParseError``。"""
