from __future__ import annotations

import time

from pgam.core.models import AppSettings
from pgam.desktop.app import MainWindow, ServiceBridge
from pgam.desktop.startup import MemoryStartupManager, default_startup_command
from pgam.services.services import AppService
from pgam.storage.secrets import InMemorySecretStore


def test_auto_start_defaults_on_and_command_is_hidden():
    assert AppSettings().auto_start_enabled is True
    command = default_startup_command()
    assert command.endswith("--hidden")
    assert '"' in command


def test_settings_page_can_enable_and_disable_auto_start(tmp_path, monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    service = AppService(data_dir=tmp_path, secrets=InMemorySecretStore())
    bridge = ServiceBridge(service)
    manager = MemoryStartupManager(enabled=False)
    window = MainWindow(bridge, startup_manager=manager)
    assert window.auto_start.isChecked() is True
    window._apply_settings(AppSettings(auto_start_enabled=True))
    assert window.auto_start.isChecked() is False

    window.auto_start.setChecked(False)
    window.save_settings()
    deadline = time.monotonic() + 5
    rows = []
    while time.monotonic() < deadline:
        app.processEvents()
        rows = service.db.query(
            "SELECT value FROM app_config WHERE key='auto_start_enabled'"
        )
        if not manager.enabled and rows and rows[0]["value"] == "False":
            break
        time.sleep(0.02)
    assert manager.enabled is False
    assert rows[0]["value"] == "False"

    window.auto_start.setChecked(True)
    window.save_settings()
    deadline = time.monotonic() + 5
    rows = []
    while time.monotonic() < deadline:
        app.processEvents()
        rows = service.db.query(
            "SELECT value FROM app_config WHERE key='auto_start_enabled'"
        )
        if manager.enabled and rows and rows[0]["value"] == "True":
            break
        time.sleep(0.02)
    assert manager.enabled is True
    assert rows[0]["value"] == "True"

    window.exit_app()
    app.processEvents()
    bridge.shutdown()
    service.db.close_all_connections()
