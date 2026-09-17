import pytest

from pgam.core.models import AppSettings, MonitorTask, SourceType
from pgam.storage.database import Database, utc_now
from pgam.storage.repositories import SettingsRepository, TaskRepository
from pgam.storage.secrets import InMemorySecretStore


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "app.db")


def make_task():
    now = utc_now()
    return MonitorTask(
        0, "测试任务", "https://example.edu.cn/list.htm", SourceType.AUTO, "{}", 30, True,
        None, None, 0, now, now, now,
    )


def test_task_crud_and_minimum_interval(db):
    repo = TaskRepository(db)
    task = repo.create(make_task())
    assert repo.list()[0].id == task.id
    task.check_interval_minutes = 1
    updated = repo.update(task)
    assert updated.check_interval_minutes == 5
    repo.delete(updated.id)
    assert repo.list() == []


def test_settings_round_trip(db):
    repo = SettingsRepository(db)
    settings = AppSettings(llm_base_url="https://api.example.com/v1", smtp_port=465, immediate_email=False)
    repo.save(settings)
    loaded = repo.load()
    assert loaded.llm_base_url == "https://api.example.com/v1"
    assert loaded.smtp_port == 465
    assert loaded.immediate_email is False


def test_secret_store_round_trip():
    store = InMemorySecretStore()
    store.set("llm", "api_key", "secret")
    assert store.get("llm", "api_key") == "secret"
