import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pgam.desktop.app import MainWindow, ServiceBridge, TaskDialog  # noqa: E402
from pgam.services.services import AppService  # noqa: E402
from pgam.storage.secrets import InMemorySecretStore  # noqa: E402


def test_desktop_window_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    service = AppService(data_dir=tmp_path / "data", secrets=InMemorySecretStore())
    bridge = ServiceBridge(service)
    window = MainWindow(bridge)
    window.show()
    app.processEvents()
    assert window.tabs.count() == 4
    assert all(window.tabs.tabText(index).strip() for index in range(4))
    window.exit_app()
    app.processEvents()
    bridge.shutdown()


def test_task_dialog_returns_typed_source_enum(monkeypatch):
    from pgam.core.models import SourceType

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    dialog = TaskDialog()
    dialog.name.setText("Graduate school")
    dialog.url.setText("https://example.edu.cn/list.htm")
    _, value = dialog.value()
    assert value.source_type is SourceType.AUTO
    dialog.deleteLater()
    app.processEvents()



def test_gui_create_task_and_manual_check(tmp_path, monkeypatch):
    import httpx

    from pgam.core.models import TaskCreateInput
    from pgam.services.services import AppService
    from pgam.sources.fetcher import Fetcher

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    html = """
    <html><body><ul>
      <li><a href='/news/1'>Admission announcement one</a><span>2026-09-01</span></li>
      <li><a href='/news/2'>Admission announcement two</a><span>2026-09-02</span></li>
      <li><a href='/news/3'>Admission announcement three</a><span>2026-09-03</span></li>
    </ul></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list.htm":
            return httpx.Response(200, text=html, headers={"Content-Type": "text/html; charset=utf-8"})
        return httpx.Response(200, text="<html><body><article>Detail</article></body></html>")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    service = AppService(
        data_dir=tmp_path / "data",
        secrets=InMemorySecretStore(),
        fetcher=Fetcher(client=client, same_host_interval=0, retries=0),
    )
    bridge = ServiceBridge(service)
    window = MainWindow(bridge)
    window.show()

    def process_until(condition, timeout_seconds: float = 5):
        import time

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return True
            time.sleep(0.02)
        return False

    window.bridge.call(
        lambda: service.task_service.create_task(
            TaskCreateInput(name="Graduate school", url="https://example.edu.cn/list.htm")
        )
    )
    assert process_until(lambda: window.task_table.rowCount() == 1)
    assert window.task_table.item(0, 1).text() == "Graduate school"

    window.task_table.selectRow(0)
    window.check_task()
    assert process_until(lambda: service.task_repo.get(1) is not None and service.task_repo.get(1).last_checked_at is not None)
    import asyncio

    run = asyncio.run(service.recent_runs())[0]
    assert service.task_repo.get(1).last_success_at is not None, run.error_message

    window.exit_app()
    app.processEvents()
    bridge.shutdown()


