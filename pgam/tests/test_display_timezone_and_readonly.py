from __future__ import annotations

from datetime import UTC, datetime

from pgam.core.models import AppSettings
from pgam.desktop.app import MainWindow, ServiceBridge
from pgam.desktop.startup import MemoryStartupManager
from pgam.services.services import AppService
from pgam.storage.secrets import InMemorySecretStore


def test_display_timezone_setting_converts_utc_values(tmp_path, monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QAbstractItemView, QApplication

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    service = AppService(data_dir=tmp_path, secrets=InMemorySecretStore())
    bridge = ServiceBridge(service)
    window = MainWindow(bridge, startup_manager=MemoryStartupManager(enabled=True))

    settings = AppSettings(display_timezone="Asia/Shanghai")
    window._apply_settings(settings)
    assert window.display_timezone.currentData() == "Asia/Shanghai"

    value = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert window._format_display_time(value) == "2026-01-01 08:00:00 UTC+08:00"
    assert window._format_display_time(None) == "-"

    # Diagnostics must be inspectable but not editable.
    assert window.run_table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert window.diagnostic_text.isReadOnly() is True
    assert window.task_table.columnCount() == 10

    window.exit_app()
    app.processEvents()
    bridge.shutdown()
    service.db.close_all_connections()
