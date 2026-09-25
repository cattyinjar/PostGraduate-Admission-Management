from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets as secure_random
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from pgam.core.models import AdapterConfig, AppSettings, SourceType, TaskCreateInput
from pgam.services.services import AppService
from pgam.storage.secrets import KeyringSecretStore

EXPECTED_SETTINGS = AppSettings(
    default_interval_minutes=17,
    display_timezone="Asia/Shanghai",
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


def _credential_values(data_dir: Path) -> tuple[str, str]:
    seed = hashlib.sha256(data_dir.name.encode("utf-8")).hexdigest()
    return f"test-llm-{seed}", f"test-smtp-{seed}"


def _store(service: str) -> KeyringSecretStore:
    return KeyringSecretStore(service=service)


def _config_keys(service: AppService) -> set[str]:
    return {row["key"] for row in service.db.query("SELECT key FROM app_config")}


async def _write_configuration(data_dir: Path, service_name: str) -> dict[str, Any]:
    llm_key, smtp_password = _credential_values(data_dir)
    service = AppService(data_dir=data_dir, secrets=_store(service_name))
    try:
        await service.settings_service.save(
            EXPECTED_SETTINGS,
            llm_api_key=f"{llm_key}-initial",
            smtp_password=f"{smtp_password}-initial",
        )
        await service.settings_service.save(
            EXPECTED_SETTINGS,
            llm_api_key=llm_key,
            smtp_password=smtp_password,
        )
        await service.task_service.create_task(
            TaskCreateInput(
                name="\u914d\u7f6e\u6301\u4e45\u5316\u6d4b\u8bd5\u4efb\u52a1",
                url="https://persistence.test/list.htm",
                source_type=SourceType.RSS,
                check_interval_minutes=11,
                enabled=False,
                keywords=["\u63a8\u514d", "\u8003\u7814", "config persistence"],
                enable_llm_summary=False,
                adapter=EXPECTED_ADAPTER,
            )
        )
        keys = _config_keys(service)
        return {
            "settings_keys_covered": keys == {field.name for field in fields(AppSettings)},
            "configuration_row_count": len(keys),
        }
    finally:
        await service.stop()
        service.db.close_all_connections()


async def _read_and_validate(data_dir: Path, service_name: str) -> dict[str, Any]:
    expected_llm_key, expected_smtp_password = _credential_values(data_dir)
    service = AppService(data_dir=data_dir, secrets=_store(service_name))
    try:
        settings = await service.settings_service.load()
        settings_exact = asdict(settings) == asdict(EXPECTED_SETTINGS)
        llm_key_exact = await service.settings_service.llm_api_key() == expected_llm_key
        smtp_password_exact = await service.settings_service.smtp_password() == expected_smtp_password

        # GUI sends None when password fields are left blank; this must preserve credentials.
        await service.settings_service.save(settings, llm_api_key=None, smtp_password=None)
        blank_preserved = (
            await service.settings_service.llm_api_key() == expected_llm_key
            and await service.settings_service.smtp_password() == expected_smtp_password
        )

        task = service.task_repo.get(1)
        task_exact = bool(
            task
            and task.name == "\u914d\u7f6e\u6301\u4e45\u5316\u6d4b\u8bd5\u4efb\u52a1"
            and task.url == "https://persistence.test/list.htm"
            and task.source_type == SourceType.RSS
            and task.check_interval_minutes == 11
            and task.enabled is False
            and task.keywords == ["\u63a8\u514d", "\u8003\u7814", "config persistence"]
            and task.enable_llm_summary is False
            and task.adapter == EXPECTED_ADAPTER
        )
        raw_rows = {
            row["key"]: row["value"]
            for row in service.db.query("SELECT key,value FROM app_config")
        }
        expected_raw = {
            "default_interval_minutes": "17",
            "display_timezone": "Asia/Shanghai",
            "auto_start_enabled": "False",
            "email_subject_prefix": EXPECTED_SETTINGS.email_subject_prefix,
            "immediate_email": "False",
            "llm_base_url": EXPECTED_SETTINGS.llm_base_url,
            "llm_model": EXPECTED_SETTINGS.llm_model,
            "llm_timeout_seconds": "77",
            "llm_max_output_tokens": "3456",
            "smtp_host": EXPECTED_SETTINGS.smtp_host,
            "smtp_port": "465",
            "smtp_username": EXPECTED_SETTINGS.smtp_username,
            "smtp_sender": EXPECTED_SETTINGS.smtp_sender,
            "smtp_recipient": EXPECTED_SETTINGS.smtp_recipient,
            "smtp_use_tls": "False",
        }
        secret_material = (expected_llm_key.encode("utf-8"), expected_smtp_password.encode("utf-8"))
        credential_material_absent = all(
            secret not in path.read_bytes()
            for secret in secret_material
            for path in data_dir.rglob("*")
            if path.is_file()
        )
        return {
            "settings_exact_after_process_restart": settings_exact,
            "raw_app_config_exact": raw_rows == expected_raw,
            "app_config_keys_exact": _config_keys(service) == {field.name for field in fields(AppSettings)},
            "llm_credential_exact_after_process_restart": llm_key_exact,
            "smtp_credential_exact_after_process_restart": smtp_password_exact,
            "blank_gui_password_preserves_credentials": blank_preserved,
            "task_configuration_exact_after_process_restart": task_exact,
            "credential_material_absent_from_data_files": credential_material_absent,
        }
    finally:
        await service.stop()
        service.db.close_all_connections()


async def _cleanup(service_name: str) -> None:
    store = _store(service_name)
    store.delete("llm", "api_key")
    store.delete("smtp", "password")


def _run_probe(mode: str, data_dir: Path | None = None, service_name: str | None = None) -> dict[str, Any]:
    if mode == "cleanup":
        asyncio.run(_cleanup(service_name or ""))
        return {"cleaned": True}
    if data_dir is None or service_name is None:
        raise ValueError("data directory and service name are required")
    if mode == "write":
        return asyncio.run(_write_configuration(data_dir, service_name))
    if mode == "read":
        return asyncio.run(_read_and_validate(data_dir, service_name))
    raise ValueError(f"Unsupported probe mode: {mode}")


def verify(project_root: Path) -> dict[str, Any]:
    temporary_dir = Path(tempfile.mkdtemp(prefix="pgam-config-persistence-"))
    service_name = f"PostGraduateAdmissionMonitor:Verification:{secure_random.token_hex(12)}"
    try:
        command = [
            sys.executable,
            "-m",
            "pgam.testing.verify_config_persistence",
        ]

        def probe(mode: str) -> dict[str, Any]:
            result = subprocess.run(
                [*command, "--mode", mode, "--data-dir", str(temporary_dir), "--service", service_name],
                cwd=project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Probe {mode} failed without exposing configuration values")
            return json.loads(result.stdout.strip().splitlines()[-1])

        write_result = probe("write")
        read_result = probe("read")
        checks = {
            **{key: bool(value) for key, value in write_result.items() if key != "configuration_row_count"},
            **read_result,
            "configuration_row_count_is_complete": write_result["configuration_row_count"]
            == len(fields(AppSettings)),
        }
        return {
            "storage_backends": {
                "non_sensitive_settings": "SQLite app_config",
                "task_settings": "SQLite monitor_task",
                "llm_api_key": "Windows Credential Manager",
                "smtp_password": "Windows Credential Manager",
            },
            "checks": checks,
            "all_passed": all(checks.values()),
        }
    finally:
        try:
            cleanup = subprocess.run(
                [
                    *([sys.executable, "-m", "pgam.testing.verify_config_persistence"]),
                    "--mode",
                    "cleanup",
                    "--service",
                    service_name,
                ],
                cwd=project_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            if cleanup.returncode != 0:
                raise RuntimeError("Credential cleanup probe failed")
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["verify", "write", "read", "cleanup"])
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--service")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if args.mode == "verify":
        result = verify(project_root)
    elif args.mode == "cleanup":
        result = _run_probe(args.mode, service_name=args.service)
    else:
        result = _run_probe(args.mode, args.data_dir, args.service)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.mode == "verify" else None))
    return 0 if args.mode != "verify" or result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
