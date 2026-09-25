from __future__ import annotations

import threading
import time

from pgam.core.models import SourceType, TaskCreateInput
from pgam.desktop.app import ServiceBridge
from pgam.services.services import AppService
from pgam.storage.secrets import InMemorySecretStore
from pgam.testing.platform import LocalAdmissionPlatform


class ServiceWorkerFixture:
    def __init__(self):
        self.marker = threading.Event()
        self.stopped = threading.Event()

    async def start(self):
        loop = __import__("asyncio").get_running_loop()
        loop.call_later(0.05, self.marker.set)

    async def stop(self):
        self.stopped.set()


def test_service_worker_keeps_scheduled_asyncio_tasks_alive():
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication

    from pgam.desktop.app import ServiceWorker

    app = QCoreApplication.instance() or QCoreApplication([])
    fixture = ServiceWorkerFixture()
    worker = ServiceWorker(fixture)
    worker.start()
    try:
        assert fixture.marker.wait(2), "Scheduled asyncio task did not run without a GUI job"
    finally:
        worker.stop()
        worker.wait(5000)
    assert fixture.stopped.is_set()
    app.processEvents()


def test_scheduler_runs_due_task_without_manual_gui_action(tmp_path, monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    with LocalAdmissionPlatform() as platform:
        service = AppService(data_dir=tmp_path, secrets=InMemorySecretStore())
        settings = service.settings_repo.load()
        settings.immediate_email = False
        service.settings_repo.save(settings)
        task = __import__("asyncio").run(
            service.task_service.create_task(
                TaskCreateInput(
                    name="Automatic scheduler test",
                    url=platform.site_a.list_url,
                    source_type=SourceType.STATIC_LIST,
                    enable_llm_summary=False,
                )
            )
        )
        service.scheduler.scan_interval = 0.05
        bridge = ServiceBridge(service)
        try:
            deadline = time.monotonic() + 5
            rows = []
            while time.monotonic() < deadline:
                app.processEvents()
                rows = service.db.query(
                    "SELECT status FROM run_log WHERE task_id=?",
                    (task.id,),
                )
                if rows:
                    break
                time.sleep(0.02)
            assert rows, "Scheduler did not execute a due task without a manual GUI action"
        finally:
            bridge.shutdown()
            service.db.close_all_connections()
