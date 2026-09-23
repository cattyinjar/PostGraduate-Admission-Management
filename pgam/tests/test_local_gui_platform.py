from __future__ import annotations

import asyncio
import json
import time

import pytest

from pgam.core.models import SourceType, TaskCreateInput
from pgam.desktop.app import MainWindow, ServiceBridge
from pgam.services.services import AppService
from pgam.sources.fetcher import Fetcher
from pgam.storage.secrets import InMemorySecretStore
from pgam.testing.platform import LocalAdmissionPlatform
from pgam.testing.verify_local_monitor import MustNotBeCalledNotifier, MustNotBeCalledSummarizer

pytest.importorskip("PySide6")


def _process_until(app, condition, timeout: float = 10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def _wait_successful_task(app, service, task_id, timeout: float = 10):
    return _process_until(
        app,
        lambda: (
            lambda task: task is not None
            and task.last_checked_at is not None
            and task.last_success_at is not None
        )(service.task_repo.get(task_id)),
        timeout,
    )


def test_real_local_platform_through_desktop_gui(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    with LocalAdmissionPlatform() as platform:
        service = AppService(
            data_dir=tmp_path / "gui-data",
            secrets=InMemorySecretStore(),
            fetcher=Fetcher(same_host_interval=0, retries=0),
            summarizer_factory=lambda settings, key: MustNotBeCalledSummarizer(),
            notifier_factory=lambda settings, password: MustNotBeCalledNotifier(),
        )
        settings = asyncio.run(service.settings_service.load())
        settings.immediate_email = False
        asyncio.run(service.settings_service.save(settings))

        bridge = ServiceBridge(service)
        window = MainWindow(bridge)
        window.show()

        async def create_tasks():
            task_ids = []
            for site in platform.sites:
                task = await service.task_service.create_task(
                    TaskCreateInput(
                        name=site.name,
                        url=site.list_url,
                        source_type=SourceType.RSS if site.kind == "rss" else SourceType.STATIC_LIST,
                        check_interval_minutes=30,
                        enabled=True,
                        enable_llm_summary=False,
                    )
                )
                task_ids.append(task.id)
            return task_ids

        task_ids = asyncio.run(create_tasks())
        assert _process_until(app, lambda: window.task_table.rowCount() == 3)

        # Establish baselines through the same GUI ?????? action used by a user.
        for row in range(window.task_table.rowCount()):
            task_id = int(window.task_table.item(row, 0).text())
            window.task_table.selectRow(row)
            window.check_task()
            assert _wait_successful_task(app, service, task_id)
        assert _process_until(app, lambda: len(asyncio.run(service.list_events())) == 0)

        platform.site_a.publish(
            "GUI Site A new admission notice",
            "GUI-SITE-A-NEW-DETAIL-MARKER Recommended admission system opens.",
        )
        platform.site_b.publish(
            "GUI Site B new admission notice",
            "GUI-SITE-B-NEW-DETAIL-MARKER Check the updated requirements.",
            attachment_name="gui-site-b.pdf",
        )
        platform.site_c.publish(
            "GUI Site C new admission notice",
            "GUI-SITE-C-NEW-DETAIL-MARKER RSS links to a new detail page.",
        )

        # Trigger the actual GUI action behind the ?????? button.
        for row in range(window.task_table.rowCount()):
            task_id = int(window.task_table.item(row, 0).text())
            window.task_table.selectRow(row)
            window.check_task()
            assert _wait_successful_task(app, service, task_id)

        assert _process_until(
            app,
            lambda: len(asyncio.run(service.list_events())) == 3,
        )
        window.refresh_events()
        assert _process_until(app, lambda: window.event_table.rowCount() == 3)

        events = asyncio.run(service.list_events())
        by_task = {event.task_id: event for event in events}
        markers = {
            task_ids[0]: "GUI-SITE-A-NEW-DETAIL-MARKER",
            task_ids[1]: "GUI-SITE-B-NEW-DETAIL-MARKER",
            task_ids[2]: "GUI-SITE-C-NEW-DETAIL-MARKER",
        }
        assert {event.status.value for event in events} == {"fetched"}
        for task_id, marker in markers.items():
            event = by_task[task_id]
            assert event.item_title.startswith("GUI Site ")
            assert marker in event.detail_text

        site_b_event = by_task[task_ids[1]]
        attachments = json.loads(site_b_event.detail_attachments_json)
        assert len(attachments) == 1
        assert attachments[0]["name"] == "gui-site-b.pdf"

        assert service.db.query("SELECT count(*) c FROM summary_record")[0]["c"] == 0
        assert service.db.query("SELECT count(*) c FROM notification_record")[0]["c"] == 0

        rendered_events = [
            (window.event_table.item(row, 3).text(), window.event_table.item(row, 2).text())
            for row in range(window.event_table.rowCount())
        ]
        assert len(rendered_events) == 3
        assert all(status == "fetched" for _, status in rendered_events)

        window.exit_app()
        app.processEvents()
        bridge.shutdown()
        service.db.close_all_connections()



