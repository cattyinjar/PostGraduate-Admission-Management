import asyncio
import json

import httpx
import pytest

from pgam.intelligence.client import LLMConfig, OpenAICompatibleSummarizer, SummaryUnavailableError


def make_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_summarizer(handler) -> OpenAICompatibleSummarizer:
    return OpenAICompatibleSummarizer(
        LLMConfig("https://llm.example.com/v1", "sk-test-secret", "test-model", 2, 500),
        make_client(handler),
    )


def test_llm_success_and_json_fence():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer sk-test-secret"
        content = "```json\n" + json.dumps({
            "relevance": "high",
            "category": "推免招生",
            "summary": "这是摘要。",
            "key_dates": [{"name": "报名", "time": "2026-09-25"}],
            "important_links": ["https://example.edu.cn/a"],
        }, ensure_ascii=False) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    result = asyncio.run(make_summarizer(handler).summarize("标题", "正文", "https://example.edu.cn"))
    assert result.relevance == "high"
    assert result.category == "推免招生"
    assert result.key_dates[0]["name"] == "报名"
    assert len(calls) == 1


def test_llm_invalid_json_retries_once():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        content = "not-json" if len(calls) == 1 else json.dumps({"summary": "第二次成功"}, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    result = asyncio.run(make_summarizer(handler).summarize("标题", "正文", None))
    assert result.summary == "第二次成功"
    assert len(calls) == 2


def test_llm_http_400_does_not_retry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, json={"error": "bad request"})

    summarizer = make_summarizer(handler)
    with pytest.raises(SummaryUnavailableError):
        asyncio.run(summarizer.summarize("标题", "正文", None))
    assert len(calls) == 1
