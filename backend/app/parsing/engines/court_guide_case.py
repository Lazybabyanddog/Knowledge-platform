"""法院指导案例解析引擎（HTML）。

面向最高人民法院公报、裁判文书一类"一个页面一份裁判文书"的 HTML，在通用
HTML 结构引擎之上再做一层语义识别：

- ``p.c_title`` 的多行标题合并为案例标题，并拆出法院、文书种类与公报来源；
- ``p.c_c_content`` 识别案号；
- ``p.c_content`` 按当事人、审理经过、案件事实、诉讼请求、原审裁判、上诉理由、
  答辩意见、本院认为、裁判结果分类；
- ``p.c_r_content`` 识别审判人员、书记员与裁判日期；
- ``【审判长简介】`` 一类简介段落单独成节。

每个块都带上 ``section`` 元数据，并在小节切换处插入二级标题，使后续切分拿到的
标题路径（案例名 / 本院认为 / 裁判结果）更贴近法律检索的阅读习惯。

可配置项（其余沿用 HTML 结构引擎）：
- ``content_selector``：正文容器，默认 ``#gb_content``，未命中回退 ``body``
- ``emit_section_headings``：是否插入小节标题，默认开启
- ``keep_signature``：是否保留落款段落，默认开启
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.domain.enums import BlockType
from app.parsing.base import EngineResult, ParsedBlock, ParseEngine, ParseInput
from app.parsing.engines.html_dom import HtmlDomEngine
from app.parsing.registry import register_engine

DEFAULT_CONTENT_SELECTOR = "#gb_content"
CASE_TITLE_SECTION = "case_title"
CASE_NO_SECTION = "case_no"

SECTION_LABELS: dict[str, str] = {
    "parties": "当事人",
    "procedure": "审理经过",
    "facts": "案件事实",
    "claims": "诉讼请求",
    "original": "原审裁判",
    "appeal": "上诉理由",
    "reply": "答辩意见",
    "reasoning": "本院认为",
    "result": "裁判结果",
    "signature": "审判人员",
    "profile": "审判长简介",
}

CJK = r"\u4e00-\u9fa5〇○，。、；：？！（）《》“”‘’"
CJK_SPACE = re.compile(rf"(?<=[{CJK}])\s+(?=[{CJK}])")
SEGMENT_TAIL = re.compile(
    r"^(?P<court>[\u4e00-\u9fa5]{2,16}?人民法院)"
    r"(?P<kind>民事|刑事|行政|赔偿|执行)?"
    r"(?P<doc>判决书|裁定书|调解书|决定书)$"
)
GAZETTE_TAIL = re.compile(r"^(?P<name>[\u4e00-\u9fa5]{2,20}公报)$")

CASE_NO = re.compile(r"^[（(]\d{4}[）)][\u4e00-\u9fa50-9A-Za-z]{1,24}号$")
PROFILE_LINE = re.compile(r"^[【\[][^】\]]{0,10}简介[】\]]$")

PARTY_LEAD = re.compile(
    r"^(?:上诉人|被上诉人|原审原告|原审被告|再审申请人|再审被申请人|第三人|"
    r"原告|被告|法定代表人|委托代理人|委托诉讼代理人|负责人|住所地|住址)"
    r"[^：:]{0,14}[：:]"
)
RESULT_MARK = re.compile(r"判决如下|裁定如下|决定如下")
REASONING = re.compile(r"^(?:综上[，,]\s*)?(?:本院|最高人民法院)(?:经审理|经审查|审理|审查)?认为")
ORIGINAL_VIEW = re.compile(
    r"^(?:[\u4e00-\u9fa5]{2,20}?人民法院|原审法院|一审法院)(?:经审理|经审查|审理|审查)?认为"
)
FACTS = re.compile(r"^(?:[\u4e00-\u9fa5]{2,20}?法院)?(?:经审理|经审查)?查明")
PROCEDURE = re.compile(r"向本院提起上诉|本院依法组成|公开开庭审理|现已审理终结|本院受理")
CLAIMS = re.compile(r"起诉认为|起诉称|(?<![上])诉称|诉讼请求|请求判令")
REPLY = re.compile(r"答辩认为|答辩称|辩称")
APPEAL = re.compile(r"提起上诉称|上诉称|上诉请求|上诉理由")

SIGNATURE_SPACE = re.compile(r"[\s\u3000]+")
JUDGE_LINE = re.compile(r"^(审判长|审判员|代理审判员|人民陪审员|执行员)([\u4e00-\u9fa5·]{2,10})$")
CLERK_LINE = re.compile(r"^(书记员|法官助理)([\u4e00-\u9fa5·]{2,10})$")
DATE_LINE = re.compile(
    r"^[〇零一二三四五六七八九○0-9]{4}年"
    r"[〇零一二三四五六七八九十0-9]{1,2}月"
    r"[〇零一二三四五六七八九十0-9]{1,3}日$"
)


def _strip_cjk_spaces(text: str) -> str:
    """去掉中文之间被 HTML 合并出来的空格，保留中英文之间的空格。"""
    previous = None
    while previous != text:
        previous, text = text, CJK_SPACE.sub("", text)
    return text.strip()


def _split_title(raw: str) -> tuple[str, str | None, str | None, str | None]:
    """把合并后的标题拆成（案例标题, 法院, 文书种类, 公报来源）。

    公报页面习惯把"案例名 / 法院+文书种类"或"…公报"拆成多行，通用引擎会按空格
    拼在一起；这里从尾部逐段剥掉这些非案例名部分。
    """
    segments = [segment for segment in raw.split(" ") if segment]
    court: str | None = None
    document_kind: str | None = None
    source: str | None = None

    while len(segments) > 1:
        tail = segments[-1].replace("\u3000", "")
        match = SEGMENT_TAIL.match(tail)
        if match:
            court = match.group("court")
            document_kind = f"{match.group('kind') or ''}{match.group('doc') or ''}" or None
            segments.pop()
            continue
        gazette = GAZETTE_TAIL.match(tail)
        if gazette:
            source = gazette.group("name")
            court = court or source.removesuffix("公报").removeprefix("中华人民共和国")
            segments.pop()
            continue
        break

    title = _strip_cjk_spaces("".join(segments)) or _strip_cjk_spaces(raw)
    return title, court, document_kind, source


def _signature_role(text: str) -> tuple[str, str] | None:
    """识别落款行，返回（角色, 取值）；不是落款返回 ``None``。"""
    compact = SIGNATURE_SPACE.sub("", text)
    judge = JUDGE_LINE.match(compact)
    if judge:
        return "judge", judge.group(2)
    clerk = CLERK_LINE.match(compact)
    if clerk:
        return "clerk", clerk.group(2)
    if DATE_LINE.match(compact):
        return "judgment_date", compact
    return None


def _classify(text: str, current: str | None) -> str | None:
    """按裁判文书的固定话术判断段落归属；无法判断时沿用上一小节。"""
    head = text[:40]
    if PARTY_LEAD.match(text):
        return "parties"
    if RESULT_MARK.search(text):
        return "result"
    if REASONING.match(text):
        return "reasoning"
    if ORIGINAL_VIEW.match(text):
        return "original"
    if FACTS.match(text) and not CLAIMS.search(head):
        return "facts"
    if PROCEDURE.search(text):
        return "procedure"
    if CLAIMS.search(head):
        return "claims"
    if REPLY.search(head):
        return "reply"
    if APPEAL.search(head):
        return "appeal"
    return current


@dataclass(slots=True)
class _Structure:
    """收集结构化结果：块列表 + 小节顺序 + 案例元数据。"""

    emit_headings: bool = True
    section: str | None = None
    blocks: list[ParsedBlock] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    judges: list[str] = field(default_factory=list)
    clerks: list[str] = field(default_factory=list)
    judgment_date: str | None = None
    case_title: str | None = None
    case_no: str | None = None
    court: str | None = None
    document_kind: str | None = None
    source: str | None = None

    def enter(self, section: str | None, *, heading: bool = True) -> None:
        if not section or section == self.section:
            return
        self.section = section
        if section not in self.sections:
            self.sections.append(section)
        label = SECTION_LABELS.get(section)
        if heading and self.emit_headings and label:
            self.blocks.append(
                ParsedBlock(
                    BlockType.TITLE,
                    label,
                    page_no=1,
                    level=2,
                    metadata={"section": section, "synthetic": True},
                )
            )

    def add(self, block: ParsedBlock, *, section: str | None = None) -> None:
        code = section or self.section
        if code:
            block.metadata = {**block.metadata, "section": code}
        self.blocks.append(block)


def _structure_blocks(
    blocks: list[ParsedBlock], state: _Structure, *, keep_signature: bool
) -> None:
    for block in blocks:
        text = block.content.strip()

        if not state.blocks and block.block_type == BlockType.TITLE and text:
            title, court, kind, source = _split_title(text)
            state.case_title = title
            state.court = court
            state.document_kind = kind
            state.source = source
            state.blocks.append(
                ParsedBlock(
                    BlockType.TITLE,
                    title,
                    page_no=1,
                    level=1,
                    metadata={"section": CASE_TITLE_SECTION},
                )
            )
            continue

        if block.block_type == BlockType.PARAGRAPH:
            if not state.case_no and CASE_NO.match(text):
                state.case_no = text
                state.add(
                    ParsedBlock(BlockType.PARAGRAPH, text, page_no=1),
                    section=CASE_NO_SECTION,
                )
                continue

            if PROFILE_LINE.match(text):
                state.enter("profile")
                continue

            signature = _signature_role(text) if text else None
            if signature is not None:
                if not keep_signature:
                    continue
                role, value = signature
                state.enter("signature")
                if role == "judge":
                    state.judges.append(value)
                elif role == "clerk":
                    state.clerks.append(value)
                else:
                    state.judgment_date = value
                state.add(
                    ParsedBlock(BlockType.PARAGRAPH, text, page_no=1, metadata={"role": role})
                )
                continue

            state.enter(_classify(text, state.section))
            state.add(ParsedBlock(BlockType.PARAGRAPH, text, page_no=1))
            continue

        if block.block_type == BlockType.TITLE:
            # 页面自带的小标题：原样保留，并结束当前小节
            state.section = None
            state.blocks.append(block)
            continue

        state.add(block)


@register_engine
class CourtGuideCaseEngine(ParseEngine):
    name = "court_guide_case"
    display_name = "法院指导案例解析"
    doc_types = ("html",)

    def parse(self, source: ParseInput) -> EngineResult:
        config = dict(source.config or {})
        config.setdefault("content_selector", DEFAULT_CONTENT_SELECTOR)
        config.setdefault("merge_titles", True)

        base = HtmlDomEngine().parse(
            ParseInput(
                data=source.data,
                file_name=source.file_name,
                mime_type=source.mime_type,
                doc_type=source.doc_type,
                config=config,
            )
        )
        state = _Structure(emit_headings=bool(config.get("emit_section_headings", True)))
        keep_signature = bool(config.get("keep_signature", True))
        _structure_blocks(base.blocks, state, keep_signature=keep_signature)

        warnings = list(base.warnings)
        if not state.case_title:
            warnings.append("未识别到案例标题")
        if not state.case_no:
            warnings.append("未识别到案号")
        if not any(block.block_type == BlockType.PARAGRAPH for block in state.blocks):
            warnings.append("未从 HTML 中提取到有效正文")

        stats = {
            **base.stats,
            "engine_profile": self.name,
            "char_count": sum(len(block.content) for block in state.blocks),
            "case_title": state.case_title,
            "case_no": state.case_no,
            "court": state.court,
            "document_kind": state.document_kind,
            "source": state.source,
            "judges": state.judges,
            "clerks": state.clerks,
            "judgment_date": state.judgment_date,
            "case_sections": state.sections,
            "section_heading_count": sum(
                1 for block in state.blocks if block.metadata.get("synthetic")
            ),
        }
        return EngineResult(blocks=state.blocks, warnings=warnings, stats=stats)
