"""DeepSeek 封装（OpenAI 兼容协议）。所有调用失败都降级，不阻塞主流程。"""
from __future__ import annotations

import json
import logging

from openai import OpenAI

from app.config import settings

log = logging.getLogger("aibi.llm")
_client: OpenAI | None = None


def client() -> OpenAI | None:
    global _client
    if _client is None:
        cfg = settings()
        if not cfg["api_key"]:
            return None
        _client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=60, max_retries=1)
    return _client


def available() -> bool:
    return bool(settings()["api_key"])


def chat(messages: list[dict], temperature: float = 0.0, json_mode: bool = False, max_tokens: int = 1024) -> str | None:
    c = client()
    if c is None:
        return None
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    try:
        resp = c.chat.completions.create(
            model=settings()["model"],
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return resp.choices[0].message.content
    except Exception as e:  # noqa: BLE001
        log.warning("LLM 调用失败: %s", e)
        return None


def chat_json(messages: list[dict], temperature: float = 0.0) -> dict | None:
    raw = chat(messages, temperature=temperature, json_mode=True)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("LLM 返回非 JSON: %s", raw[:200])
        return None
