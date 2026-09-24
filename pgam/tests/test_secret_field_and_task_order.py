from __future__ import annotations

import asyncio

from pgam.core.models import MonitorTask, SourceType
from pgam.desktop.app import SecretField
from pgam.services.services import AppService
from pgam.storage.database import utc_now
from pgam.storage.secrets import InMemorySecretStore


def test_task_repository_lists_in_creation_order(tmp_path):
    async def scenario():
        service = AppService(data_dir=tmp_path, secrets=InMemorySecretStore())
        try:
            names = []
            now = utc_now()
            for index in range(4):
                task = service.task_repo.create(
                    MonitorTask(
                        id=0,
                        name=f"task-{index}",
                        url=f"https://example.edu.cn/{index}.htm",
                        source_type=SourceType.STATIC_LIST,
                        adapter_config_json="{}",
                        check_interval_minutes=30,
                        enabled=True,
                        last_checked_at=None,
                        last_success_at=None,
                        failure_count=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
                names.append(task.name)
            assert [task.name for task in service.task_repo.list()] == names
        finally:
            await service.stop()
            service.db.close_all_connections()

    asyncio.run(scenario())


def test_secret_field_masks_saved_length_and_edits_only_after_replace(monkeypatch):
    pytest = __import__("pytest")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLineEdit

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])

    field = SecretField("not saved")
    assert field.display_button.text() == "\u663e\u793a"
    assert field.copy_button.text() == "\u590d\u5236"
    assert field.replace_button.text() == "\u66f4\u6362"
    field.set_masked_length(12)
    assert field.edit.placeholderText() == ""
    assert field.display_button.text() == "\u663e\u793a"
    assert field.edit.isReadOnly() is True
    assert field.edit.echoMode() == QLineEdit.EchoMode.Password
    assert len(field.edit.displayText()) == 12
    assert field.value_for_save() is None
    assert field.display_button.isEnabled() is True
    assert field.copy_button.isEnabled() is True

    field.begin_edit()
    assert field.edit.placeholderText() == "\u8f93\u5165\u65b0\u7684\u4fdd\u5bc6\u5185\u5bb9"
    assert field.edit.isReadOnly() is False
    field.edit.setText("new-secret")
    assert field.value_for_save() == "new-secret"

    field.set_secret_for_display("abcd")
    assert field.display_button.text() == "\u9690\u85cf"
    assert field.edit.isReadOnly() is True
    assert field.edit.echoMode() == QLineEdit.EchoMode.Normal
    assert field.edit.text() == "abcd"
    field.hide_secret()
    assert field.display_button.text() == "\u663e\u793a"
    assert field.edit.echoMode() == QLineEdit.EchoMode.Password
    assert len(field.edit.displayText()) == 4
    assert field.value_for_save() is None
    field.deleteLater()
    app.processEvents()



def test_desktop_sources_do_not_contain_question_mark_corruption():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "desktop"
    for source in root.glob("*.py"):
        assert ("?" * 2) not in source.read_text(encoding="utf-8-sig")
