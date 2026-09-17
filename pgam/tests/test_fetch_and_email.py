import asyncio
import smtplib
from email.message import EmailMessage

import httpx
import pytest

from pgam.notifications.email import EmailNotifier, SmtpConfig
from pgam.sources.fetcher import Fetcher, FetchError, FetchRequest


def make_fetcher(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return Fetcher(client=client, same_host_interval=0, **kwargs)


def test_fetch_success_and_encoding():
    def handler(request):
        assert request.headers["User-Agent"].startswith("PostGraduateAdmissionMonitor/")
        return httpx.Response(200, content="notice".encode("gbk"), headers={"Content-Type": "text/html"})

    fetcher = make_fetcher(handler, retries=0)
    result = asyncio.run(fetcher.fetch(FetchRequest(1, "https://example.edu.cn/a")))
    assert result.http_status == 200
    assert result.raw_content == "notice".encode("gbk")
    asyncio.run(fetcher.aclose())


def test_fetch_5xx_retries_and_4xx_does_not_retry():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/retry":
            return httpx.Response(500) if len(calls) == 1 else httpx.Response(200, text="ok")
        return httpx.Response(404)

    fetcher = make_fetcher(handler, retries=1)
    result = asyncio.run(fetcher.fetch(FetchRequest(1, "https://example.edu.cn/retry")))
    assert result.http_status == 200
    assert len(calls) == 2
    with pytest.raises(FetchError):
        asyncio.run(fetcher.fetch(FetchRequest(1, "https://example.edu.cn/missing")))
    assert len(calls) == 3
    asyncio.run(fetcher.aclose())


def test_email_success(monkeypatch):
    sent = []

    class FakeSMTP:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            return (250, b"ok")

        def starttls(self, context=None):
            return (220, b"ready")

        def login(self, username, password):
            assert password == "secret"

        def send_message(self, message: EmailMessage):
            sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", lambda host, port, timeout: FakeSMTP())
    notifier = EmailNotifier(
        SmtpConfig("smtp.example.com", 587, "user@example.com", "secret", "from@example.com", "to@example.com", True, "[TEST]")
    )
    asyncio.run(notifier.send_test())
    assert len(sent) == 1
    assert sent[0]["Subject"].startswith("[TEST]")
