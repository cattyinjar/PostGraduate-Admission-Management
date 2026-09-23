from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pgam.core.models import SourceType, TaskCreateInput
from pgam.services.services import AppService
from pgam.sources.fetcher import Fetcher
from pgam.storage.secrets import InMemorySecretStore
from pgam.testing.platform import LocalAdmissionPlatform
from pgam.testing.verify_deepseek import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, _read_token
from pgam.testing.verify_email import RECIPIENT, SMTP_HOSTS, _read_credentials


async def _run_scenario(
    data_dir: Path,
    platform: LocalAdmissionPlatform,
    token: str,
    smtp_host: str,
    credentials,
) -> dict[str, Any]:
    service = AppService(
        data_dir=data_dir,
        secrets=InMemorySecretStore(),
        fetcher=Fetcher(same_host_interval=0, retries=0),
    )
    try:
        settings = await service.settings_service.load()
        settings.llm_base_url = DEEPSEEK_BASE_URL
        settings.llm_model = DEEPSEEK_MODEL
        settings.llm_timeout_seconds = 90
        settings.llm_max_output_tokens = 1200
        settings.immediate_email = True
        settings.smtp_host = smtp_host
        settings.smtp_port = 465
        settings.smtp_username = credentials.account
        settings.smtp_sender = credentials.sender
        settings.smtp_recipient = RECIPIENT
        settings.smtp_use_tls = True
        await service.settings_service.save(
            settings,
            llm_api_key=token,
            smtp_password=credentials.password,
        )

        task_ids: dict[str, int] = {}
        for site in (
            platform.site_a,
            platform.site_b,
            platform.site_c,
        ):
            task = await service.task_service.create_task(
                TaskCreateInput(
                    name=site.name,
                    url=site.list_url,
                    source_type=SourceType.RSS if site.kind == "rss" else SourceType.STATIC_LIST,
                    check_interval_minutes=30,
                    enabled=True,
                    enable_llm_summary=True,
                )
            )
            task_ids[site.name] = task.id

        baseline_runs = {
            name: await service.monitoring.run_task(task_id)
            for name, task_id in task_ids.items()
        }
        baseline_event_count = len(await service.list_events())

        for site in (platform.site_a, platform.site_b, platform.site_c):
            site.change_decoration("Full-chain decorative change; no new notice is expected")
        decorative_runs = {
            name: await service.monitoring.run_task(task_id)
            for name, task_id in task_ids.items()
        }
        decorative_event_count = len(await service.list_events())

        platform.site_a.publish(
            "Full chain Site A recommended admission notice",
            "FULL-CHAIN-SITE-A-MARKER The online recommendation system opens on 26 September 2026 at 09:00. "
            "Submit the application form, transcript, and two recommendation letters before 30 September 2026.",
        )
        platform.site_b.publish(
            "Full chain Site B postgraduate admission notice",
            "FULL-CHAIN-SITE-B-MARKER The graduate admission briefing will be held on 28 September 2026. "
            "Applicants should upload the required materials and review the attachment before the deadline.",
            attachment_name="full-chain-site-b-attachment.pdf",
        )
        platform.site_c.publish(
            "Full chain Site C RSS admission notice",
            "FULL-CHAIN-SITE-C-MARKER This RSS item links to a new admission detail page with application dates.",
        )

        final_runs = {
            name: await service.monitoring.run_task(task_id)
            for name, task_id in task_ids.items()
        }

        events = await service.list_events()
        events_by_task = {event.task_id: event for event in events}
        summaries = service.db.query(
            """
            SELECT s.event_id,s.model,s.prompt_version,s.result_json,s.summary_text
            FROM summary_record s ORDER BY s.event_id
            """
        )
        notifications = service.db.query(
            """
            SELECT event_id,channel,recipient,status,sent_at,error_message
            FROM notification_record ORDER BY event_id
            """
        )
        notifications_by_event = {row["event_id"]: row for row in notifications}

        site_reports: list[dict[str, Any]] = []
        markers = {
            platform.site_a.name: "FULL-CHAIN-SITE-A-MARKER",
            platform.site_b.name: "FULL-CHAIN-SITE-B-MARKER",
            platform.site_c.name: "FULL-CHAIN-SITE-C-MARKER",
        }
        for site in (platform.site_a, platform.site_b, platform.site_c):
            task_id = task_ids[site.name]
            event = events_by_task.get(task_id)
            summary = next((row for row in summaries if event and row["event_id"] == event.id), None)
            notification = notifications_by_event.get(event.id) if event else None
            structured = json.loads(summary["result_json"]) if summary else {}
            attachments = json.loads(event.detail_attachments_json) if event else []
            site_reports.append(
                {
                    "name": site.name,
                    "source_type": "rss" if site.kind == "rss" else "static_list",
                    "detected": bool(event and event.item_title.startswith("Full chain Site ")),
                    "detail_fetched": bool(event and markers[site.name] in (event.detail_text or "")),
                    "llm_summarized": bool(summary and summary["model"] == DEEPSEEK_MODEL),
                    "structured_relevance": structured.get("relevance"),
                    "structured_category": structured.get("category"),
                    "summary_preview": (summary["summary_text"][:120] + "...") if summary and len(summary["summary_text"]) > 120 else (summary["summary_text"] if summary else ""),
                    "email_notified": bool(notification and notification["status"] == "success"),
                    "email_recipient": notification["recipient"] if notification else None,
                    "attachment_extracted": True if site.kind != "table_gbk" else len(attachments) == 1,
                    "event_status": event.status.value if event else "missing",
                }
            )

        credential_bytes = [
            token.encode("utf-8"),
            credentials.password.encode("utf-8"),
        ]
        persisted_files = list(data_dir.rglob("*"))
        credential_material_found = any(
            secret in path.read_bytes()
            for secret in credential_bytes
            for path in persisted_files
            if path.is_file()
        )
        all_runs_successful = all(
            run.status.value == "success"
            for run_group in (baseline_runs, decorative_runs, final_runs)
            for run in run_group.values()
        )
        site_checks_passed = all(
            report["detected"]
            and report["detail_fetched"]
            and report["llm_summarized"]
            and report["email_notified"]
            and report["attachment_extracted"]
            and report["event_status"] == "notified"
            and report["structured_relevance"] in {"high", "medium", "low"}
            and bool(report["structured_category"])
            and report["email_recipient"] == RECIPIENT
            for report in site_reports
        )
        counts_passed = (
            baseline_event_count == 0
            and decorative_event_count == 0
            and len(events) == 3
            and len(summaries) == 3
            and len(notifications) == 3
            and all(row["status"] == "success" for row in notifications)
        )

        return {
            "sites": site_reports,
            "baseline_event_count": baseline_event_count,
            "decorative_event_count": decorative_event_count,
            "event_count": len(events),
            "summary_record_count": len(summaries),
            "notification_record_count": len(notifications),
            "all_runs_successful": all_runs_successful,
            "site_checks_passed": site_checks_passed,
            "counts_passed": counts_passed,
            "credential_material_found_in_local_data": credential_material_found,
        }
    finally:
        await service.stop()
        service.db.close_all_connections()


async def _wrapped_scenario(
    data_dir: Path,
    platform: LocalAdmissionPlatform,
    token: str,
    smtp_host: str,
    credentials,
) -> dict[str, Any]:
    return await _run_scenario(data_dir, platform, token, smtp_host, credentials)

def verify(project_root: Path) -> dict[str, Any]:
    token = _read_token(project_root)
    credentials = _read_credentials(project_root)
    smtp_host = SMTP_HOSTS[credentials.sender.rsplit("@", 1)[1].lower()]
    temporary_dir = Path(tempfile.mkdtemp(prefix="pgam-full-chain-"))
    try:
        with LocalAdmissionPlatform() as platform:
            result = asyncio.run(
                _wrapped_scenario(temporary_dir, platform, token, smtp_host, credentials)
            )
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)

    checks = {
        "all_runs_successful": result["all_runs_successful"],
        "baseline_clean": result["baseline_event_count"] == 0,
        "decorative_changes_ignored": result["decorative_event_count"] == 0,
        "three_events_detected": result["event_count"] == 3,
        "three_llm_summaries_persisted": result["summary_record_count"] == 3,
        "three_emails_accepted": result["notification_record_count"] == 3,
        "site_checks_passed": result["site_checks_passed"],
        "counts_passed": result["counts_passed"],
        "credential_material_absent": result["credential_material_found_in_local_data"] is False,
        "temporary_data_cleaned": not temporary_dir.exists(),
    }
    return {
        "llm_provider": "DeepSeek",
        "llm_model": DEEPSEEK_MODEL,
        "smtp_host": smtp_host,
        "smtp_ssl": True,
        "recipient": RECIPIENT,
        "checks": checks,
        "sites": result["sites"],
        "all_passed": all(checks.values()),
    }


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    try:
        result = verify(project_root)
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
