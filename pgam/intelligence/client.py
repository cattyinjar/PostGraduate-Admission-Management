from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..core.models import SummaryResult

PROMPT_VERSION = "v1"

SYSTEM_PROMPT = """你是中国研究生招生通知分析助手。请只输出一个合法 JSON 对象，不要 Markdown 代码块。
字段要求：
relevance: high/medium/low
category: 推免招生/考研招生/复试调剂/其他
summary: 3到5句中文摘要
key_dates: [{name, time}]，没有则为 []
action_required: 需要考生执行的关键动作，没有则为空字符串
target_audience: 适用考生范围
important_links: 重要链接数组
attachments: 附件名称数组"""

USER_TEMPLATE = """通知标题：{title}
通知链接：{url}
通知正文：
{text}"""


class SummaryUnavailableError(RuntimeError):
    pass


@dataclass(slots=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60
    max_output_tokens: int = 1600


class OpenAICompatibleSummarizer:
    def __init__(self, config: LLMConfig, client: httpx.AsyncClient | None = None):
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)

    async def summarize(self, title: str, text: str, url: str | None) -> SummaryResult:
        if not self.config.base_url or not self.config.model or not self.config.api_key:
            raise SummaryUnavailableError("LLM is not configured")
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(title=title, url=url or "not provided", text=text)},
            ],
            "temperature": 0.1,
            "max_tokens": self.config.max_output_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        invalid_json_seen = False
        for attempt in range(3):
            try:
                response = await self._client.post(endpoint, json=payload, headers=headers)
                if response.status_code >= 500 and attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                if response.status_code >= 400:
                    raise SummaryUnavailableError(f"LLM HTTP {response.status_code}")
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                return self._parse(content)
            except SummaryUnavailableError as exc:
                if "invalid JSON" in str(exc) and not invalid_json_seen:
                    invalid_json_seen = True
                    continue
                raise
            except (TypeError, KeyError, json.JSONDecodeError, httpx.HTTPError) as exc:
                if attempt >= 2:
                    raise SummaryUnavailableError("LLM request failed") from exc
                await asyncio.sleep(0.5 * (2**attempt))
        raise SummaryUnavailableError("LLM request failed")

    def _parse(self, content: str) -> SummaryResult:
        try:
            data = self._json_object(content)
            return self._validate(data)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise SummaryUnavailableError("LLM returned invalid JSON") from exc

    @staticmethod
    def _json_object(content: str) -> dict[str, Any]:
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("No JSON object in response")
            text = text[start : end + 1]
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError("LLM response is not an object")
        return value

    @staticmethod
    def _validate(data: dict[str, Any]) -> SummaryResult:
        relevance = str(data.get("relevance", "medium")).lower()
        if relevance not in {"high", "medium", "low"}:
            relevance = "medium"
        key_dates_value = data.get("key_dates", [])
        if not isinstance(key_dates_value, list):
            key_dates_value = []
        key_dates = [
            {"name": str(item.get("name", "")), "time": str(item.get("time", ""))}
            for item in key_dates_value
            if isinstance(item, dict)
        ]
        links = data.get("important_links", [])
        attachments = data.get("attachments", [])
        return SummaryResult(
            relevance=relevance,
            category=str(data.get("category", "其他")),
            summary=str(data.get("summary", "")).strip(),
            key_dates=key_dates,
            action_required=str(data.get("action_required", "")),
            target_audience=str(data.get("target_audience", "")),
            important_links=[str(item) for item in links] if isinstance(links, list) else [],
            attachments=[str(item) for item in attachments] if isinstance(attachments, list) else [],
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
