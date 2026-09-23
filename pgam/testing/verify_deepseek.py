from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pgam.core.models import SourceType, TaskCreateInput
from pgam.core.ports import NotifierProtocol
from pgam.intelligence.client import SummaryUnavailableError
from pgam.services.services import AppService
from pgam.sources.fetcher import Fetcher
from pgam.storage.secrets import InMemorySecretStore
from pgam.testing.platform import LocalAdmissionPlatform

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"


class MustNotBeCalledNotifier(NotifierProtocol):
    async def send_event(self, task, title, url, summary):
        raise AssertionError("DeepSeek verification must not send email")

    async def send_task_failure(self, task, error):
        raise AssertionError("DeepSeek verification must not send failure email")


def _read_token(project_root: Path) -> str:
    token_path = project_root / "token.txt"
    token = token_path.read_text(encoding="utf-8").strip().lstrip("\ufeff").strip()
    if not token or any(character in token for character in "\r\n\t "):
        raise ValueError("token.txt must contain one non-empty API token and no extra whitespace")
    return token


async def _run_scenario(
    data_dir: Path, platform: LocalAdmissionPlatform, token: str
) -> dict[str, Any]:
    fetcher = Fetcher(same_host_interval=0, retries=0)
    service = AppService(
        data_dir=data_dir,
        secrets=InMemorySecretStore(),
        fetcher=fetcher,
        notifier_factory=lambda settings, password: MustNotBeCalledNotifier(),
    )
    try:
        settings = await service.settings_service.load()
        settings.llm_base_url = DEEPSEEK_BASE_URL
        settings.llm_model = DEEPSEEK_MODEL
        settings.llm_timeout_seconds = 90
        settings.llm_max_output_tokens = 1200
        settings.immediate_email = False
        await service.settings_service.save(settings, llm_api_key=token)

        real_task = await service.task_service.create_task(
            TaskCreateInput(
                name="DeepSeek live summarization",
                url=platform.site_a.list_url,
                source_type=SourceType.STATIC_LIST,
                enable_llm_summary=True,
            )
        )
        baseline = await service.monitoring.run_task(real_task.id)
        if baseline.status.value != "success":
            raise RuntimeError(f"Baseline run failed: {baseline.error_message}")
        if await service.list_events():
            raise AssertionError("Baseline unexpectedly created a processing event")

        announcement = platform.site_a.publish(
            "DeepSeek live admission summarization test",
            "DEEPSEEK-LIVE-LLM-MARKER The recommended admission application system will open on 26 September 2026. "
            "Applicants must submit their undergraduate transcript, personal statement, and two recommendation letters "
            "before 30 September 2026. This local page is used only to verify that the software can send a newly "
            "detected announcement to DeepSeek and persist a structured Chinese summary.",
        )

        final = await service.monitoring.run_task(real_task.id)
        if final.status.value != "success":
            raise RuntimeError(f"Final run failed: {final.error_message}")

        events = await service.list_events()
        matching = [event for event in events if event.item_title == announcement.title]
        if len(matching) != 1:
            raise AssertionError(f"Expected one matching event, found {len(matching)}")
        event = matching[0]

        summary_row = service.db.query(
            "SELECT model,prompt_version,result_json,summary_text FROM summary_record WHERE event_id=? ORDER BY id DESC LIMIT 1",
            (event.id,),
        )
        if len(summary_row) != 1:
            raise AssertionError("DeepSeek response was not persisted to summary_record")
        row = summary_row[0]
        result = json.loads(row["result_json"])
        notification_count = service.db.query("SELECT count(*) c FROM notification_record")[0]["c"]
        config_values = [str(row[1]) for row in service.db.query("SELECT key,value FROM app_config")]

        return {
            "event_status": event.status.value,
            "summary_record_created": True,
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "summary_text": row["summary_text"],
            "structured_relevance": result.get("relevance"),
            "structured_category": result.get("category"),
            "notification_records": int(notification_count),
            "token_absent_from_database": all(token not in value for value in config_values)
            and token not in row["result_json"]
            and token not in row["summary_text"],
        }
    finally:
        await service.stop()
        service.db.close_all_connections()


async def _wrapped_scenario(data_dir: Path, token: str) -> dict[str, Any]:
    with LocalAdmissionPlatform() as platform:
        return await _run_scenario(data_dir, platform, token)


def verify(project_root: Path) -> dict[str, Any]:
    token = _read_token(project_root)
    temporary_dir = Path(tempfile.mkdtemp(prefix="pgam-deepseek-live-"))
    try:
        result = asyncio.run(_wrapped_scenario(temporary_dir, token))
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)

    checks = {
        "event_summarized": result["event_status"] == "summarized",
        "summary_record_created": result["summary_record_created"] is True,
        "model_correct": result["model"] == DEEPSEEK_MODEL,
        "prompt_version_recorded": result["prompt_version"] == "v1",
        "summary_non_empty": bool(result["summary_text"].strip()),
        "structured_fields_valid": result["structured_relevance"] in {"high", "medium", "low"}
        and bool(result["structured_category"]),
        "email_not_sent": result["notification_records"] == 0,
        "token_absent_from_database": result["token_absent_from_database"] is True,
        "temporary_data_cleaned": not temporary_dir.exists(),
    }
    return {
        "provider": "DeepSeek",
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEEPSEEK_MODEL,
        "checks": checks,
        "all_passed": all(checks.values()),
        "details": result,
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    try:
        result = verify(project_root)
    except SummaryUnavailableError as exc:
        print(json.dumps({"all_passed": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {"all_passed": False, "error_type": type(exc).__name__},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
