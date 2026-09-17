import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from pgam.desktop.app import MainWindow, ServiceBridge  # noqa: E402
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
