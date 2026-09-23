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
from pgam.testing.realistic_platform import RealisticAdmissionPlatform
from pgam.testing.verify_deepseek import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, _read_token
from pgam.testing.verify_email import RECIPIENT, SMTP_HOSTS, _read_credentials


def _publication_body(site_name: str) -> str:
    marker = f"{site_name.upper()}-FULL-CHAIN-DETAIL-MARKER"
    return (
        f"{marker} This is a synthetic postgraduate admission notice for structure-replica site {site_name}. "
        "The online application system opens on 26 September 2026 at 09:00 and closes on 30 September 2026 at 17:00. "
        "Applicants must submit an application form, undergraduate transcript, personal statement, and two recommendation letters. "
        "A briefing will be held on 28 September 2026. This content is synthetic and is used only for end-to-end testing."
    )


async def _run_scenario(
    data_dir: Path,
    platform: RealisticAdmissionPlatform,
    token: str,
    smtp_host: str,
    credentials: Any,
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
        for site in platform.sites:
            task = await service.task_service.create_task(
                TaskCreateInput(
                    name=site.name,
                    url=site.list_url,
                    source_type=SourceType.STATIC_LIST,
                    check_interval_minutes=30,
                    enabled=True,
                    keywords=[],
                    enable_llm_summary=True,
                )
            )
            task_ids[site.name] = task.id

        baseline_runs = {
            site.name: await service.monitoring.run_task(task_ids[site.name])
            for site in platform.sites
        }
        baseline_event_count = len(await service.list_events())
        baseline_counts = {
            site.name: int(
                service.db.query(
                    "SELECT count(*) AS count FROM publication_item WHERE task_id=?",
                    (task_ids[site.name],),
                )[0]["count"]
            )
            for site in platform.sites
        }

        for site in platform.sites:
            site.change_decoration("Realistic full-chain decorative change; no new notice")
        decorative_runs = {
            site.name: await service.monitoring.run_task(task_ids[site.name])
            for site in platform.sites
        }
        decorative_event_count = len(await service.list_events())

        published = {
            site.name: site.publish(
                f"{site.name} full-chain postgraduate admission notice",
                _publication_body(site.name),
            )
            for site in platform.sites
        }
        final_runs = {
            site.name: await service.monitoring.run_task(task_ids[site.name])
            for site in platform.sites
        }

        events = await service.list_events()
        events_by_site: dict[str, list[Any]] = {site.name: [] for site in platform.sites}
        for event in events:
            task = await service.task_service.get_task(event.task_id)
            events_by_site[task.name].append(event)

        summary_rows = service.db.query(
            """
            SELECT event_id,model,prompt_version,result_json,summary_text
            FROM summary_record ORDER BY event_id
            """
        )
        summaries_by_event = {row["event_id"]: row for row in summary_rows}
        notification_rows = service.db.query(
            """
            SELECT event_id,channel,recipient,status,sent_at,error_message
            FROM notification_record ORDER BY event_id
            """
        )
        notifications_by_event = {row["event_id"]: row for row in notification_rows}

        site_reports: list[dict[str, Any]] = []
        for site in platform.sites:
            event = events_by_site[site.name][0] if len(events_by_site[site.name]) == 1 else None
            summary = summaries_by_event.get(event.id) if event else None
            notification = notifications_by_event.get(event.id) if event else None
            structured = json.loads(summary["result_json"]) if summary else {}
            marker = f"{site.name.upper()}-FULL-CHAIN-DETAIL-MARKER"
            site_reports.append(
                {
                    "name": site.name,
                    "origin_url": site.origin_url,
                    "baseline_items": baseline_counts[site.name],
                    "detected": bool(event and event.item_title == published[site.name].title),
                    "detail_fetched": bool(event and marker in (event.detail_text or "")),
                    "llm_summarized": bool(
                        summary
                        and summary["model"] == DEEPSEEK_MODEL
                        and summary["prompt_version"] == "v1"
                        and summary["summary_text"].strip()
                    ),
                    "relevance": structured.get("relevance"),
                    "category": structured.get("category"),
                    "summary_preview": (
                        summary["summary_text"][:100] + "..."
                        if summary and len(summary["summary_text"]) > 100
                        else summary["summary_text"] if summary else ""
                    ),
                    "email_notified": bool(
                        notification
                        and notification["recipient"] == RECIPIENT
                        and notification["status"] == "success"
                    ),
                    "event_status": event.status.value if event else "missing",
                }
            )

        secrets_in_files = [token.encode("utf-8"), credentials.password.encode("utf-8")]
        credential_material_found = any(
            secret in path.read_bytes()
            for secret in secrets_in_files
            for path in data_dir.rglob("*")
            if path.is_file()
        )
        all_runs_successful = all(
            run.status.value == "success"
            for group in (baseline_runs, decorative_runs, final_runs)
            for run in group.values()
        )
        site_checks_passed = all(
            report["detected"]
            and report["detail_fetched"]
            and report["llm_summarized"]
            and report["email_notified"]
            and report["event_status"] == "notified"
            and report["relevance"] in {"high", "medium", "low"}
            and bool(report["category"])
            for report in site_reports
        )
        counts_passed = (
            baseline_event_count == 0
            and decorative_event_count == 0
            and len(events) == 16
            and len(summary_rows) == 16
            and len(notification_rows) == 16
            and all(row["status"] == "success" for row in notification_rows)
        )
        return {
            "site_count": len(platform.sites),
            "sites": site_reports,
            "baseline_event_count": baseline_event_count,
            "decorative_event_count": decorative_event_count,
            "event_count": len(events),
            "summary_record_count": len(summary_rows),
            "notification_record_count": len(notification_rows),
            "all_runs_successful": all_runs_successful,
            "site_checks_passed": site_checks_passed,
            "counts_passed": counts_passed,
            "credential_material_found": credential_material_found,
        }
    finally:
        await service.stop()
        service.db.close_all_connections()


def verify(project_root: Path) -> dict[str, Any]:
    token = _read_token(project_root)
    credentials = _read_credentials(project_root)
    smtp_host = SMTP_HOSTS[credentials.sender.rsplit("@", 1)[1].lower()]
    temporary_dir = Path(tempfile.mkdtemp(prefix="pgam-realistic-full-chain-"))
    try:
        with RealisticAdmissionPlatform() as platform:
            result = asyncio.run(
                _run_scenario(temporary_dir, platform, token, smtp_host, credentials)
            )
    finally:
        shutil.rmtree(temporary_dir, ignore_errors=True)

    checks = {
        "site_count_is_16": result["site_count"] == 16,
        "all_runs_successful": result["all_runs_successful"],
        "baseline_clean": result["baseline_event_count"] == 0,
        "decorative_changes_ignored": result["decorative_event_count"] == 0,
        "sixteen_events_detected": result["event_count"] == 16,
        "sixteen_llm_summaries_persisted": result["summary_record_count"] == 16,
        "sixteen_emails_accepted": result["notification_record_count"] == 16,
        "site_checks_passed": result["site_checks_passed"],
        "counts_passed": result["counts_passed"],
        "credential_material_absent": result["credential_material_found"] is False,
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
