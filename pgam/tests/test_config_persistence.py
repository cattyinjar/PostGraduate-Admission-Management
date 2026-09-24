from __future__ import annotations

import asyncio
from dataclasses import asdict

from pgam.core.models import AdapterConfig, AppSettings, SourceType, TaskCreateInput
from pgam.services.services import AppService
from pgam.storage.secrets import InMemorySecretStore

EXPECTED_SETTINGS = AppSettings(
    default_interval_minutes=17,
    auto_start_enabled=False,
    email_subject_prefix="\u3010\u914d\u7f6e\u6301\u4e45\u5316\u6d4b\u8bd5\u3011",
    immediate_email=False,
    llm_base_url="https://llm-persistence.test/v1",
    llm_model="persistence-test-model",
    llm_timeout_seconds=77,
    llm_max_output_tokens=3456,
    smtp_host="smtp-persistence.test",
    smtp_port=465,
    smtp_username="persistence-user",
    smtp_sender="sender@persistence.test",
    smtp_recipient="recipient@persistence.test",
    smtp_use_tls=False,
)
EXPECTED_ADAPTER = AdapterConfig(
    item_selector=".notice-item",
    title_selector=".notice-title",
    link_selector=".notice-link",
    date_selector=".notice-date",
    encoding="gbk",
    allow_insecure_tls=True,
    detect_content_updates=False,
)


def test_all_settings_and_task_configuration_survive_service_restart(tmp_path):
    async def scenario():
        secrets = InMemorySecretStore()
        first = AppService(data_dir=tmp_path, secrets=secrets)
        await first.settings_service.save(
            EXPECTED_SETTINGS,
            llm_api_key="test-llm-key",
            smtp_password="test-smtp-password",
        )
        task = await first.task_service.create_task(
            TaskCreateInput(
                name="\u914d\u7f6e\u6301\u4e45\u5316\u6d4b\u8bd5\u4efb\u52a1",
                url="https://persistence.test/list.htm",
                source_type=SourceType.RSS,
                check_interval_minutes=11,
                enabled=False,
                keywords=["\u63a8\u514d", "\u8003\u7814"],
                enable_llm_summary=False,
                adapter=EXPECTED_ADAPTER,
            )
        )
        await first.stop()
        first.db.close_all_connections()

        second = AppService(data_dir=tmp_path, secrets=secrets)
        settings = await second.settings_service.load()
        assert asdict(settings) == asdict(EXPECTED_SETTINGS)
        assert await second.settings_service.llm_api_key() == "test-llm-key"
        assert await second.settings_service.smtp_password() == "test-smtp-password"
        persisted = await second.task_service.get_task(task.id)
        assert persisted is not None
        assert persisted.adapter == EXPECTED_ADAPTER
        assert persisted.keywords == ["\u63a8\u514d", "\u8003\u7814"]
        assert persisted.enable_llm_summary is False
        await second.stop()
        second.db.close_all_connections()

    asyncio.run(scenario())


def test_blank_secret_fields_preserve_existing_credentials(tmp_path):
    async def scenario():
        secrets = InMemorySecretStore()
        service = AppService(data_dir=tmp_path, secrets=secrets)
        await service.settings_service.save(
            EXPECTED_SETTINGS,
            llm_api_key="llm-value",
            smtp_password="smtp-value",
        )
        reloaded = await service.settings_service.load()
        await service.settings_service.save(reloaded, llm_api_key=None, smtp_password=None)
        assert await service.settings_service.llm_api_key() == "llm-value"
        assert await service.settings_service.smtp_password() == "smtp-value"
        await service.stop()
        service.db.close_all_connections()

    asyncio.run(scenario())
