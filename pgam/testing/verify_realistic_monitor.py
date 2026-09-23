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
from pgam.testing.realistic_platform import RealisticAdmissionPlatform


class MustNotBeCalledSummarizer(SummarizerProtocol):
    async def summarize(self, title: str, text: str, url: str | None):
        raise AssertionError("LLM must not be called by the realistic-site detector verification")


class MustNotBeCalledNotifier(NotifierProtocol):
    async def send_event(self, task, title, url, summary):
        raise AssertionError("email must not be called by the realistic-site detector verification")

    async def send_task_failure(self, task, error):
        raise AssertionError("failure email must not be called by the realistic-site detector verification")


async def _run_scenario(data_dir: Path, platform: RealisticAdmissionPlatform) -> dict[str, Any]:
    service = AppService(
        data_dir=data_dir,
        secrets=InMemorySecretStore(),
        fetcher=Fetcher(same_host_interval=0, retries=0),
        summarizer_factory=lambda settings, key: MustNotBeCalledSummarizer(),
        notifier_factory=lambda settings, password: MustNotBeCalledNotifier(),
    )
    try:
        settings = await service.settings_service.load()
        settings.immediate_email = False
        await service.settings_service.save(settings)

        task_ids: dict[str, int] = {}
        for site in platform.sites:
            task = await service.task_service.create_task(
                TaskCreateInput(
                    name=site.name,
                    url=site.list_url,
                    source_type=SourceType.STATIC_LIST,
                    check_interval_minutes=5,
                    enabled=True,
                    keywords=[],
                    enable_llm_summary=False,
                )
            )
            task_ids[site.name] = task.id

        baseline_runs = {
            site.name: await service.monitoring.run_task(task_ids[site.name])
            for site in platform.sites
        }
        baseline_event_count = len(await service.list_events())
        baseline_counts = {
            site.name: service.db.query(
                "SELECT count(*) AS count FROM publication_item WHERE task_id=?",
                (task_ids[site.name],),
            )[0]["count"]
            for site in platform.sites
        }

        for site in platform.sites:
            site.change_decoration("Decorative realistic-site footer changed")
        decorative_runs = {
            site.name: await service.monitoring.run_task(task_ids[site.name])
            for site in platform.sites
        }
        decorative_event_count = len(await service.list_events())

        published = {
            site.name: site.publish(f"{site.name} newly published admission notice")
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

        reports: list[dict[str, Any]] = []
        for site in platform.sites:
            announcement = published[site.name]
            candidates = events_by_site[site.name]
            event = candidates[0] if len(candidates) == 1 else None
            marker = f"{site.name.upper()}-NEW-DETAIL-MARKER"
            snapshot = service.db.query(
                "SELECT extraction_result_json FROM page_snapshot WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_ids[site.name],),
            )[0]["extraction_result_json"]
            reports.append(
                {
                    "name": site.name,
                    "origin_url": site.origin_url,
                    "baseline_items": int(baseline_counts[site.name]),
                    "published_title": announcement.title,
                    "detected": bool(
                        event
                        and event.item_title == announcement.title
                        and event.item_url
                    ),
                    "detail_fetched": bool(event and event.detail_text),
                    "detail_contains": bool(event and marker in (event.detail_text or "")),
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
            and report["status"] == "fetched"
            and report["snapshot_contains_published_title"]
            for report in reports
        )
        return {
            "site_count": len(platform.sites),
            "sites": reports,
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
    with RealisticAdmissionPlatform() as platform:
        temporary_dir = Path(tempfile.mkdtemp(prefix="pgam-realistic-monitor-"))
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
