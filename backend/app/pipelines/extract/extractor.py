"""抽取器：优先用 LLM，未配置密钥时退化为确定性的规则抽取。

两者都返回同一 ``BatchExtraction`` 结构，图里只依赖接口，不关心实现。
测试可以 ``set_extractor()`` 注入脚本化输出，覆盖校验/修复分支。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol

from app.infra.llm import (
    LLMError,
    LLMPermanentError,
    LLMTransientError,
    chat_json,
    is_llm_configured,
)
from app.infra.llm.prompts import (
    ENTITY_SYSTEM,
    REPAIR_SYSTEM,
    blocks_prompt,
    ontology_prompt,
    prompt_projection,
)

logger = logging.getLogger(__name__)

_FRAGMENT_SPLIT = re.compile(r"[，。；：、,.!?;:\n\r\t（）()\[\]【】]+")
_TRIM = "《》“”\"'‘’ 　"


@dataclass
class BatchExtraction:
    raw: dict[str, Any] | None = None
    error: str | None = None


class EntityExtractor(Protocol):
    name: str

    def extract(
        self,
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction: ...

    def repair(
        self,
        raw: dict[str, Any] | None,
        issues: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction: ...


class RuleBasedExtractor:
    """无 LLM 时的兜底：按本体名称命中片段，再用关系名做弱匹配。

    产出质量有限，胜在完全确定、可离线回归；生产环境配置 LLM 后自动切换。
    """

    name = "rule_based"

    def extract(
        self,
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        entities: list[dict[str, Any]] = []
        relation_rows: list[dict[str, Any]] = []

        for block in blocks:
            block_id = str(block["id"])
            content = str(block.get("content") or "")
            fragments = [item.strip(_TRIM) for item in _FRAGMENT_SPLIT.split(content)]
            fragments = [item for item in fragments if item]
            by_class: dict[str, str] = {}

            for class_code, meta in classes.items():
                aliases = [meta.get("name") or "", *(meta.get("aliases") or [])]
                mention = self._match_mention(fragments, [item for item in aliases if item])
                if mention is None:
                    continue
                by_class[class_code] = mention
                entities.append(
                    {
                        "ref": f"{block_id}:{class_code}",
                        "class_code": class_code,
                        "name": mention,
                        "aliases": [],
                        "description": None,
                        # 规则抽取不猜属性值：结构上保持与 LLM 输出一致，值留空。
                        "attributes": {},
                        "confidence": 0.6,
                        "evidence_block_ids": [block_id],
                    }
                )

            for relation_code, meta in relations.items():
                source_class = meta.get("source_class_code")
                target_class = meta.get("target_class_code")
                if not source_class or not target_class:
                    continue
                if source_class not in by_class or target_class not in by_class:
                    continue
                evidence = self._relation_evidence(fragments, meta)
                if evidence is None:
                    continue
                relation_rows.append(
                    {
                        "source_ref": f"{block_id}:{source_class}",
                        "target_ref": f"{block_id}:{target_class}",
                        "code": relation_code,
                        "attributes": {},
                        "evidence_text": evidence,
                        "confidence": 0.55,
                        "evidence_block_id": block_id,
                    }
                )

        return BatchExtraction(raw={"entities": entities, "relations": relation_rows})

    def repair(
        self,
        raw: dict[str, Any] | None,
        issues: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        """规则抽取的修复等于"重新按更严的条件再抽一遍"。"""
        return self.extract(blocks, classes, relations)

    @staticmethod
    def _match_mention(fragments: list[str], aliases: list[str]) -> str | None:
        for fragment in fragments:
            for alias in aliases:
                if alias and alias in fragment:
                    return fragment[:40]
        return None

    @staticmethod
    def _relation_evidence(fragments: list[str], meta: dict[str, Any]) -> str | None:
        """命中的片段直接当证据原文：规则抽取的"证据"就是这句话本身。"""
        keywords = [meta.get("name") or "", *(meta.get("keywords") or [])]
        for fragment in fragments:
            if any(word and word in fragment for word in keywords):
                return fragment
        return None


class LLMExtractor:
    """OpenAI 兼容接口的结构化抽取。"""

    name = "llm"

    def extract(
        self,
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        user = "\n\n".join(
            [
                ontology_prompt(list(classes.values()), list(relations.values())),
                blocks_prompt(_payloads(blocks)),
            ]
        )
        try:
            return BatchExtraction(raw=chat_json(ENTITY_SYSTEM, user))
        except (LLMTransientError, LLMPermanentError):
            # 服务不可用不是"这一批没价值"：往上抛，让任务失败并进入重试预算，
            # 否则会变成"超时 250 次、任务成功、0 实体"。
            raise
        except LLMError as exc:
            logger.warning("LLM 抽取失败: %s", exc)
            return BatchExtraction(raw=None, error=str(exc))

    def repair(
        self,
        raw: dict[str, Any] | None,
        issues: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        classes: dict[str, dict[str, Any]],
        relations: dict[str, dict[str, Any]],
    ) -> BatchExtraction:
        projected_classes, projected_relations = prompt_projection(
            list(classes.values()), list(relations.values())
        )
        expected = (
            '{"entities":[{"ref":"","class_code":"","name":"","aliases":[],'
            '"description":"","attributes":{},"confidence":0.0,'
            '"evidence_block_ids":[]}],'
            '"relations":[{"source_ref":"","target_ref":"","code":"","attributes":{},'
            '"evidence_text":"","confidence":0.0,"evidence_block_id":""}]}'
        )
        user = json.dumps(
            {
                "期望结构": expected,
                "本体": {"classes": projected_classes, "relations": projected_relations},
                "校验问题": issues,
                "上一次输出": raw,
                "原文块": _payloads(blocks),
            },
            ensure_ascii=False,
        )
        try:
            return BatchExtraction(raw=chat_json(REPAIR_SYSTEM, user))
        except (LLMTransientError, LLMPermanentError):
            raise
        except LLMError as exc:
            logger.warning("LLM 修复失败: %s", exc)
            return BatchExtraction(raw=raw, error=str(exc))


def _payloads(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(item["id"]),
            "block_type": item.get("block_type"),
            "content": str(item.get("content") or "")[:2000],
        }
        for item in blocks
    ]


_EXTRACTOR: EntityExtractor | None = None


def get_extractor() -> EntityExtractor:
    global _EXTRACTOR
    if _EXTRACTOR is None:
        _EXTRACTOR = LLMExtractor() if is_llm_configured() else RuleBasedExtractor()
    return _EXTRACTOR


def set_extractor(extractor: EntityExtractor | None) -> None:
    """测试钩子：注入脚本化抽取器；传 None 恢复按配置选择。"""
    global _EXTRACTOR
    _EXTRACTOR = extractor


__all__ = [
    "BatchExtraction",
    "EntityExtractor",
    "LLMExtractor",
    "RuleBasedExtractor",
    "get_extractor",
    "set_extractor",
]
