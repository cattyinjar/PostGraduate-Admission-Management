import asyncio

import httpx

from pgam.core.models import SummaryResult, TaskCreateInput
from pgam.notifications.email import NullNotifier
from pgam.services.services import AppService
from pgam.sources.fetcher import Fetcher
from pgam.storage.secrets import InMemorySecretStore


class FakeSummarizer:
    async def summarize(self, title: str, text: str, url: str | None) -> SummaryResult:
        return SummaryResult(
            relevance="high",
            category="admission",
            summary=f"AI summary of {title}",
            key_dates=[{"name": "open", "time": "2026-09-25 09:00"}],
            action_required="apply soon",
        )


class RecordingNotifier(NullNotifier):
    def __init__(self):
        self.events = []

    async def send_event(self, task, title, url, summary):
        self.events.append(title)


def list_html(ids):
    rows = "".join(
        f"<li><a href='news-{item}.html'>admission announcement title {item}</a><span>2026-09-{item:02d}</span></li>"
        for item in ids
    )
    return f"<html><body><ul class='news'>{rows}</ul></body></html>"


def detail_html(item, body="application system opens soon"):
    return f"<html><head><title>admission announcement title {item}</title></head><body><article>{body}</article></body></html>"


def make_service(tmp_path, events):
    state = {"list_ids": [1, 2, 3], "detail": {1: "old body", 2: "old body", 3: "old body"}}

    def request_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list.html":
            return httpx.Response(200, text=list_html(state["list_ids"]), headers={"Content-Type": "text/html; charset=utf-8"})
        if request.url.path.startswith("/news-"):
            item = int(request.url.path.removeprefix("/news-").removesuffix(".html"))
            body = state["detail"].get(item, "new body")
            return httpx.Response(200, text=detail_html(item, body), headers={"Content-Type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(request_handler), follow_redirects=True)
    fetcher = Fetcher(client=client, same_host_interval=0, retries=0)
    service = AppService(
        data_dir=tmp_path / "data",
        secrets=InMemorySecretStore(),
        fetcher=fetcher,
        summarizer_factory=lambda settings, key: FakeSummarizer(),
        notifier_factory=lambda settings, password: events.pop() if events else RecordingNotifier(),
    )
    return service, state


async def configured_task(service):
    settings = await service.settings_service.load()
    settings.llm_base_url = "https://llm.invalid/v1"
    settings.llm_model = "fake"
    settings.smtp_host = "smtp.invalid"
    settings.smtp_recipient = "user@example.com"
    await service.settings_service.save(settings, llm_api_key="secret", smtp_password="secret")
    return await service.task_service.create_task(
        TaskCreateInput(name="graduate school", url="https://grad.example.edu.cn/list.html")
    )


def test_end_to_end_baseline_new_item_update_and_restart(tmp_path):
    async def scenario():
        first_notifier = RecordingNotifier()
        second_notifier = RecordingNotifier()
        notifiers = [first_notifier, second_notifier]
        service, state = make_service(tmp_path, notifiers)
        task = await configured_task(service)
        first = await service.monitoring.run_task(task.id)
        assert first.status.value == "success", first.error_message
        assert first_notifier.events == []

        state["list_ids"] = [99, 1, 2, 3]
        second = await service.monitoring.run_task(task.id)
        assert second.status.value == "success", second.error_message
        assert second_notifier.events == ["admission announcement title 99"]
        assert [event.status.value for event in await service.list_events()] == ["notified"]

        state["detail"][1] = "important deadline updated"
        third_notifier = RecordingNotifier()
        notifiers.append(third_notifier)
        third = await service.monitoring.run_task(task.id)
        assert third.status.value == "success", third.error_message
        assert third_notifier.events == ["admission announcement title 1"]
        assert len(await service.list_events()) == 2
        await service.stop()

        restart_notifier = RecordingNotifier()
        restart = AppService(
            data_dir=tmp_path / "data",
            secrets=InMemorySecretStore(),
            fetcher=Fetcher(
                client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
                same_host_interval=0,
                retries=0,
            ),
            summarizer_factory=lambda settings, key: FakeSummarizer(),
            notifier_factory=lambda settings, password: restart_notifier,
        )
        await restart.monitoring.process_pending_events()
        assert restart_notifier.events == []
        await restart.stop()

    asyncio.run(scenario())
