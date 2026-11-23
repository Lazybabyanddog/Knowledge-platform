"""LLM 供应商适配：只依赖 OpenAI 兼容的 ``/chat/completions`` 协议。

不绑定具体 SDK，换供应商只改 ``llm_base_url`` / ``llm_model``。
未配置 ``llm_api_key`` 时 ``is_llm_configured()`` 为 False，抽取走规则兜底，
保证离线环境也能跑通整条链路。
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)
_EMBED_MAX_ATTEMPTS = 3


class LLMError(RuntimeError):
    """模型调用或返回解析失败，可由上层决定修复或跳过。"""


class LLMTransientError(LLMError):
    """瞬时故障（超时 / 429 / 5xx / 连接重置）：值得重试，不该被降级成"跳过该批"。"""

    task_error_code = "llm_unavailable"

    def __init__(self, message: str, *, error_code: str = "llm_unavailable") -> None:
        super().__init__(message)
        self.task_error_code = error_code


class LLMPermanentError(LLMError):
    """凭据无效、请求被拒：重试只会浪费预算，直接失败并提示改配置。"""

    task_error_code = "llm_rejected"


class EmbeddingError(RuntimeError):
    """向量化失败：未配置、调用失败或返回结构异常。"""


class EmbeddingDimensionError(EmbeddingError):
    """返回向量维度与库表列定义不一致，属于配置错误，不做重试。"""


def is_llm_configured() -> bool:
    return bool(settings.llm_api_key and settings.llm_base_url)


def is_embedding_configured() -> bool:
    return bool(settings.embedding_api_key and settings.embedding_base_url)


def _openai_endpoint(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}{path}"
    return f"{base}/v1{path}"


def _endpoint() -> str:
    return _openai_endpoint(settings.llm_base_url, "/chat/completions")


def chat_json(system: str, user: str) -> dict[str, Any]:
    """要求模型输出 JSON 对象；自动剥掉 ```json 围栏与前后解释文字。

    进程内只重试"抖动"：超时、429、5xx、连接重置。凭据类错误立刻抛永久错误，
    输出不是合法 JSON 仍然抛 ``LLMError`` —— 那是内容问题，由上层 repair 处理。
    """
    if not is_llm_configured():
        raise LLMPermanentError("未配置 LLM（llm_api_key / llm_base_url 为空）")

    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    return parse_json_object(_post_chat(payload, headers))


def _post_chat(payload: dict[str, Any], headers: dict[str, str]) -> str:
    attempts = max(1, settings.llm_max_attempts)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = httpx.post(
                _endpoint(),
                json=payload,
                headers=headers,
                timeout=settings.llm_timeout_seconds,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status != 429 and status < 500:
                raise LLMPermanentError(f"模型拒绝请求（HTTP {status}）") from exc
            last_error = exc
        except httpx.TimeoutException as exc:
            last_error = exc
        except httpx.TransportError as exc:
            last_error = exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMError(f"模型返回结构异常: {exc}") from exc
        if attempt < attempts - 1:
            time.sleep(_retry_delay(attempt))

    if isinstance(last_error, httpx.TimeoutException):
        raise LLMTransientError(
            f"模型调用超时: {last_error}", error_code="llm_timeout"
        ) from last_error
    raise LLMTransientError(f"模型服务不可用: {last_error}") from last_error


def _retry_delay(attempt: int) -> float:
    raw = min(
        settings.llm_retry_max_delay_seconds,
        settings.llm_retry_base_delay_seconds * (2**attempt),
    )
    jitter = raw * settings.llm_retry_jitter_ratio
    return max(0.0, raw + random.uniform(-jitter, jitter))


async def astream_chat(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """流式对话：逐段产出文本增量。

    调用方应显式 ``aclose()``，否则客户端断开后上游连接会多活一会儿。
    """
    if not is_llm_configured():
        raise LLMError("未配置 LLM（llm_api_key / llm_base_url 为空）")

    payload = {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": messages,
        "stream": True,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.llm_timeout_seconds, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", _endpoint(), json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(content)
        if match is None:
            raise LLMError("模型输出不是合法 JSON") from None
        try:
            loaded = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError("模型输出不是合法 JSON") from exc
    if not isinstance(loaded, dict):
        raise LLMError("模型输出必须是 JSON 对象")
    return loaded


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量生成向量，返回顺序与入参一致。

    每条请求都显式带 ``dimensions``，由我们控制维度而不是依赖模型默认值；
    单批失败按 1s/2s/4s 重试，维度不符直接失败（重试无意义）。
    """
    if not texts:
        return []
    if not is_embedding_configured():
        raise EmbeddingError("未配置 embedding（embedding_api_key / embedding_base_url 为空）")

    vectors: list[list[float]] = []
    for batch in _batched(texts, max(1, settings.embedding_batch_size)):
        vectors.extend(_embed_batch(batch))
    return vectors


def _embed_batch(texts: list[str]) -> list[list[float]]:
    payload = {
        "model": settings.embedding_model,
        "input": texts,
        "dimensions": settings.embedding_dim,
    }
    headers = {
        "Authorization": f"Bearer {settings.embedding_api_key}",
        "Content-Type": "application/json",
    }
    url = _openai_endpoint(settings.embedding_base_url, "/embeddings")

    last_error: Exception | None = None
    for attempt in range(_EMBED_MAX_ATTEMPTS):
        try:
            response = httpx.post(
                url, json=payload, headers=headers, timeout=settings.embedding_timeout_seconds
            )
            response.raise_for_status()
            return _parse_embeddings(response.json(), expected=len(texts))
        except EmbeddingDimensionError:
            raise
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < _EMBED_MAX_ATTEMPTS - 1:
                time.sleep(2**attempt)
    raise EmbeddingError(f"向量化调用失败: {last_error}")


def _parse_embeddings(body: dict[str, Any], *, expected: int) -> list[list[float]]:
    items = body["data"]
    if not isinstance(items, list) or len(items) != expected:
        raise EmbeddingError(f"向量化返回条目数不符：期望 {expected}，实际 {len(items)}")
    ordered = sorted(items, key=lambda item: item.get("index", 0))
    vectors: list[list[float]] = []
    for item in ordered:
        vector = [float(value) for value in item["embedding"]]
        if len(vector) != settings.embedding_dim:
            raise EmbeddingDimensionError(
                f"向量维度不符：期望 {settings.embedding_dim}，实际 {len(vector)}"
            )
        vectors.append(vector)
    return vectors


def _batched(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


__all__ = [
    "EmbeddingDimensionError",
    "EmbeddingError",
    "LLMError",
    "LLMPermanentError",
    "LLMTransientError",
    "astream_chat",
    "chat_json",
    "embed_texts",
    "is_embedding_configured",
    "is_llm_configured",
    "parse_json_object",
]
