from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pgam.core.models import SourceType, TaskCreateInput
from pgam.core.ports import NotifierProtocol, SummarizerProtocol
from pgam.services.services import AppService
from pgam.sources.fetcher import Fetcher
from pgam.storage.secrets import InMemorySecretStore
from pgam.testing.platform import LocalAdmissionPlatform


class MustNotBeCalledSummarizer(SummarizerProtocol):
    async def summarize(self, title: str, text: str, url: str | None):
        raise AssertionError("LLM summarizer must not be called in local detection verification")


class MustNotBeCalledNotifier(NotifierProtocol):
    async def send_event(self, task, title, url, summary):
        raise AssertionError("email notifier must not be called in local detection verification")

    async def send_task_failure(self, task, error):
        raise AssertionError("failure email must not be called in local detection verification")


def _source_type(site) -> SourceType:
    return SourceType.RSS if site.kind == "rss" else SourceType.STATIC_LIST


async def _run_scenario(data_dir: Path, platform: LocalAdmissionPlatform) -> dict[str, Any]:
    fetcher = Fetcher(same_host_interval=0, retries=0)
    service = AppService(
        data_dir=data_dir,
        secrets=InMemorySecretStore(),
        fetcher=fetcher,
        summarizer_factory=lambda settings, key: MustNotBeCalledSummarizer(),
        notifier_factory=lambda settings, password: MustNotBeCalledNotifier(),
    )
    try:
        settings = await service.settings_service.load()
        settings.immediate_email = False
        await service.settings_service.save(settings)

        task_by_site = {}
        for site in platform.sites:
            task = await service.task_service.create_task(
                TaskCreateInput(
                    name=site.name,
                    url=site.list_url,
                    source_type=_source_type(site),
                    check_interval_minutes=5,
                    enabled=True,
                    keywords=[],
                    enable_llm_summary=False,
                )
            )
            task_by_site[site.name] = task.id

        baseline_runs = {
            site.name: await service.monitoring.run_task(task_by_site[site.name])
            for site in platform.sites
        }
        baseline_event_count = len(await service.list_events())

        for site in platform.sites:
            site.change_decoration("Decorative local footer changed; no new notice should be detected")
        decorative_runs = {
            site.name: await service.monitoring.run_task(task_by_site[site.name])
            for site in platform.sites
        }

        decorative_event_count = len(await service.list_events())
        published = {}
        published["site_a_static_ul"] = platform.site_a.publish(
            "Site A new admission notice",
            "SITE-A-NEW-DETAIL-MARKER The recommended admission system opens soon.",
        )
        published["site_b_table_gbk"] = platform.site_b.publish(
            "Site B new admission notice",
            "SITE-B-NEW-DETAIL-MARKER Please review the updated postgraduate requirements.",
            attachment_name="site-b-attachment.pdf",
        )
        published["site_c_rss"] = platform.site_c.publish(
            "Site C new admission notice",
            "SITE-C-NEW-DETAIL-MARKER This RSS entry links to a new detail page.",
        )

        final_runs = {
            site.name: await service.monitoring.run_task(task_by_site[site.name])
            for site in platform.sites
        }

        events = await service.list_events()
        events_by_site: dict[str, list[Any]] = {site.name: [] for site in platform.sites}
        for event in events:
            task = await service.task_service.get_task(event.task_id)
            events_by_site[task.name].append(event)

        site_reports = []
        for site in platform.sites:
            announcement = published[site.name]
            site_events = events_by_site[site.name]
            event = site_events[0] if len(site_events) == 1 else None
            item_count = service.db.query(
                "SELECT count(*) AS count FROM publication_item WHERE task_id=?",
                (task_by_site[site.name],),
            )[0]["count"]
            snapshot = service.db.query(
                "SELECT extraction_result_json FROM page_snapshot WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_by_site[site.name],),
            )[0]["extraction_result_json"]
            event_url = announcement.id and f"{site.base_url}/{site.slug}/news/{announcement.id}.htm"
            detected = (
                len(site_events) == 1
                and event is not None
                and event.item_title == announcement.title
                and event.item_url == event_url
            )
            detail_fetched = bool(event and event.detail_text)
            detail_markers = {
                "site_a_static_ul": "SITE-A-NEW-DETAIL-MARKER",
                "site_b_table_gbk": "SITE-B-NEW-DETAIL-MARKER",
                "site_c_rss": "SITE-C-NEW-DETAIL-MARKER",
            }
            detail_contains = bool(event and detail_markers[site.name] in event.detail_text)
            attachments = []
            if event:
                try:
                    attachments = json.loads(event.detail_attachments_json or "[]")
                except json.JSONDecodeError:
                    attachments = []
            expected_attachment = site.kind == "table_gbk" and len(attachments) == 1
            site_reports.append(
                {
                    "name": site.name,
                    "source_type": "rss" if site.kind == "rss" else "static_list",
                    "baseline_items": int(item_count) - 1,
                    "published_title": announcement.title,
                    "detected": detected,
                    "detail_fetched": detail_fetched,
                    "detail_contains": detail_contains,
                    "attachment_extracted": True if site.kind != "table_gbk" else expected_attachment,
                    "status": event.status.value if event else "missing",
                    "snapshot_contains_published_title": announcement.title in snapshot,
                }
            )

        summary_count = service.db.query("SELECT count(*) AS count FROM summary_record")[0]["count"]
        notification_count = service.db.query(
            "SELECT count(*) AS count FROM notification_record"
        )[0]["count"]
        runs_successful = all(
            run.status.value == "success"
            for runs in (baseline_runs, decorative_runs, final_runs)
            for run in runs.values()
        )
        site_checks_passed = all(
            report["detected"]
            and report["detail_fetched"]
            and report["detail_contains"]
            and report["attachment_extracted"]
            and report["status"] == "fetched"
            and report["snapshot_contains_published_title"]
            and report["baseline_items"] == 3
            for report in site_reports
        )
        return {
            "sites": site_reports,
            "event_count": len(events),
            "baseline_event_count": int(baseline_event_count),
            "decorative_event_count": int(decorative_event_count),
            "summary_records": int(summary_count),
            "notification_records": int(notification_count),
            "all_runs_successful": runs_successful,
            "decorative_changes_created_events": len(events) != len(platform.sites),
            "all_passed": (
                runs_successful
                and site_checks_passed
                and baseline_event_count == 0
                and decorative_event_count == 0
                and len(events) == len(platform.sites)
                and summary_count == 0
                and notification_count == 0
            ),
        }
    finally:
        await service.stop()
        service.db.close_all_connections()


def verify() -> dict[str, Any]:
    with LocalAdmissionPlatform() as platform:
        temporary_dir = Path(tempfile.mkdtemp(prefix="pgam-local-monitor-"))
        try:
            result = asyncio.run(_run_scenario(temporary_dir, platform))
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)
        result["temporary_data_cleaned"] = not temporary_dir.exists()
        result["all_passed"] = result["all_passed"] and result["temporary_data_cleaned"]
        return result


def main() -> int:
    result = verify()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
