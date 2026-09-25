from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..core.models import (
    AdapterConfig,
    AppSettings,
    EventStatus,
    EventType,
    MonitorTask,
    MonitorTaskView,
    ProcessingEvent,
    RunLog,
    RunStatus,
    SourceType,
    SummaryResult,
    TaskCreateInput,
    TaskUpdateInput,
)
from ..core.ports import NotifierProtocol, SecretStore, SummarizerProtocol
from ..intelligence.client import LLMConfig, OpenAICompatibleSummarizer, SummaryUnavailableError
from ..notifications.email import EmailNotifier, NotificationError, SmtpConfig
from ..sources.adapters import SourceAdapterRegistry
from ..sources.extractor import DetailExtractor
from ..sources.fetcher import Fetcher, FetchError, FetchRequest
from ..storage.database import Database, utc_now
from ..storage.repositories import (
    MonitoringRepository,
    RunLogRepository,
    SettingsRepository,
    TaskRepository,
)

SummarizerFactory = Callable[[AppSettings, str], SummarizerProtocol]
NotifierFactory = Callable[[AppSettings, str], NotifierProtocol]


def default_data_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "PostGraduateAdmissionMonitor"
    return Path.home() / ".local" / "share" / "PostGraduateAdmissionMonitor"


def _validate_url(url: str) -> str:
    raw = url.strip()
    if "](" in raw:
        left, right = raw.split("](", 1)
        candidates = [left.strip(), right.rstrip(")").strip()]
    else:
        candidates = [raw]
    candidates = [candidate.strip("<>") for candidate in candidates]
    candidate = next(
        (
            item
            for item in candidates
            if urlparse(item).scheme in {"http", "https"} and urlparse(item).netloc
        ),
        "",
    )
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be a valid http/https address")
    return candidate


def _json_or_default(value: str, default: str) -> str:
    try:
        json.loads(value)
        return value
    except (TypeError, json.JSONDecodeError):
        return default


class TaskService:
    def __init__(self, repository: TaskRepository, settings: SettingsService):
        self.repository = repository
        self.settings = settings

    async def create_task(self, item: TaskCreateInput) -> MonitorTask:
        interval = max(5, int(item.check_interval_minutes))
        now = utc_now()
        task = MonitorTask(
            id=0,
            name=item.name.strip(),
            url=_validate_url(item.url),
            source_type=item.source_type,
            adapter_config_json=json.dumps(item.adapter.to_json_dict(), ensure_ascii=False),
            check_interval_minutes=interval,
            enabled=item.enabled,
            last_checked_at=None,
            last_success_at=None,
            failure_count=0,
            created_at=now,
            updated_at=now,
            next_check_at=now if item.enabled else None,
            keywords_json=json.dumps(item.keywords, ensure_ascii=False),
            enable_llm_summary=item.enable_llm_summary,
        )
        return await asyncio.to_thread(self.repository.create, task)

    async def update_task(self, task_id: int, item: TaskUpdateInput) -> MonitorTask:
        current = await asyncio.to_thread(self.repository.get, task_id)
        if current is None:
            raise KeyError(task_id)
        current.name = item.name.strip()
        current.url = _validate_url(item.url)
        current.source_type = item.source_type
        current.adapter_config_json = json.dumps(item.adapter.to_json_dict(), ensure_ascii=False)
        current.check_interval_minutes = max(5, int(item.check_interval_minutes))
        current.enabled = item.enabled
        current.keywords_json = json.dumps(item.keywords, ensure_ascii=False)
        current.enable_llm_summary = item.enable_llm_summary
        return await asyncio.to_thread(self.repository.update, current)

    async def delete_task(self, task_id: int) -> None:
        await asyncio.to_thread(self.repository.delete, task_id)

    async def set_enabled(self, task_id: int, enabled: bool) -> None:
        task = await asyncio.to_thread(self.repository.get, task_id)
        if task is None:
            raise KeyError(task_id)
        task.enabled = enabled
        task.next_check_at = utc_now() if enabled else None
        await asyncio.to_thread(self.repository.update, task)

    async def list_tasks(self) -> list[MonitorTaskView]:
        return await asyncio.to_thread(self.repository.views)

    async def get_task(self, task_id: int) -> MonitorTask:
        task = await asyncio.to_thread(self.repository.get, task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    async def export_tasks(self, path: str | Path) -> int:
        tasks = await asyncio.to_thread(self.repository.list)
        payload = []
        for task in tasks:
            payload.append(
                {
                    "name": task.name,
                    "url": task.url,
                    "source_type": task.source_type.value,
                    "check_interval_minutes": task.check_interval_minutes,
                    "enabled": task.enabled,
                    "keywords": task.keywords,
                    "enable_llm_summary": task.enable_llm_summary,
                    "adapter": task.adapter.to_json_dict(),
                }
            )
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return len(payload)

    async def import_tasks(self, path: str | Path, replace: bool = False) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("\u4efb\u52a1\u5bfc\u5165\u6587\u4ef6\u5fc5\u987b\u662f\u6570\u7ec4")
        if replace:
            for task in await asyncio.to_thread(self.repository.list):
                await asyncio.to_thread(self.repository.delete, task.id)
        count = 0
        for row in data:
            item = TaskCreateInput(
                name=str(row["name"]),
                url=str(row["url"]),
                source_type=SourceType(str(row.get("source_type", "auto"))),
                check_interval_minutes=int(row.get("check_interval_minutes", 30)),
                enabled=bool(row.get("enabled", True)),
                keywords=[str(value) for value in row.get("keywords", [])],
                enable_llm_summary=bool(row.get("enable_llm_summary", True)),
                adapter=AdapterConfig.from_dict(row.get("adapter")),
            )
            await self.create_task(item)
            count += 1
        return count


class SettingsService:
    def __init__(self, repository: SettingsRepository, secrets: SecretStore):
        self.repository = repository
        self.secrets = secrets

    async def load(self) -> AppSettings:
        return await asyncio.to_thread(self.repository.load)

    async def save(
        self,
        settings: AppSettings,
        *,
        llm_api_key: str | None = None,
        smtp_password: str | None = None,
    ) -> None:
        settings.default_interval_minutes = max(5, settings.default_interval_minutes)
        if settings.display_timezone != "system":
            try:
                ZoneInfo(settings.display_timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("Invalid display timezone") from exc
        settings.llm_timeout_seconds = max(5, settings.llm_timeout_seconds)
        settings.llm_max_output_tokens = max(256, settings.llm_max_output_tokens)
        settings.smtp_port = 1 if settings.smtp_port <= 0 else min(65535, settings.smtp_port)
        await asyncio.to_thread(self.repository.save, settings)
        if llm_api_key is not None:
            if llm_api_key:
                await asyncio.to_thread(self.secrets.set, "llm", "api_key", llm_api_key)
            else:
                await asyncio.to_thread(self.secrets.delete, "llm", "api_key")
        if smtp_password is not None:
            if smtp_password:
                await asyncio.to_thread(self.secrets.set, "smtp", "password", smtp_password)
            else:
                await asyncio.to_thread(self.secrets.delete, "smtp", "password")

    async def llm_api_key(self) -> str:
        return await asyncio.to_thread(self.secrets.get, "llm", "api_key") or ""

    async def smtp_password(self) -> str:
        return await asyncio.to_thread(self.secrets.get, "smtp", "password") or ""

    async def llm_api_key_length(self) -> int:
        value = await asyncio.to_thread(self.secrets.get, "llm", "api_key")
        return len(value or "")

    async def smtp_password_length(self) -> int:
        value = await asyncio.to_thread(self.secrets.get, "smtp", "password")
        return len(value or "")


class MonitoringService:
    def __init__(
        self,
        db: Database,
        tasks: TaskRepository,
        runs: RunLogRepository,
        monitoring: MonitoringRepository,
        fetcher: Fetcher,
        settings: SettingsService,
        *,
        summarizer_factory: SummarizerFactory | None = None,
        notifier_factory: NotifierFactory | None = None,
        extractor: DetailExtractor | None = None,
        adapters: SourceAdapterRegistry | None = None,
        max_update_checks: int = 8,
    ) -> None:
        self.db = db
        self.tasks = tasks
        self.runs = runs
        self.monitoring = monitoring
        self.fetcher = fetcher
        self.settings = settings
        self.adapters = adapters or SourceAdapterRegistry()
        self.extractor = extractor or DetailExtractor()
        self.max_update_checks = max_update_checks
        self._task_locks: dict[int, asyncio.Lock] = {}
        self._lock_guard = asyncio.Lock()
        self._summarizer_factory = summarizer_factory or self._default_summarizer_factory
        self._notifier_factory = notifier_factory or self._default_notifier_factory

    async def _get_task_lock(self, task_id: int) -> asyncio.Lock:
        async with self._lock_guard:
            return self._task_locks.setdefault(task_id, asyncio.Lock())

    @staticmethod
    def _default_summarizer_factory(settings: AppSettings, api_key: str) -> SummarizerProtocol:
        return OpenAICompatibleSummarizer(
            LLMConfig(
                base_url=settings.llm_base_url,
                api_key=api_key,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                max_output_tokens=settings.llm_max_output_tokens,
            )
        )

    @staticmethod
    def _default_notifier_factory(settings: AppSettings, password: str) -> NotifierProtocol:
        return EmailNotifier(
            SmtpConfig(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=password,
                sender=settings.smtp_sender,
                recipient=settings.smtp_recipient,
                use_tls=settings.smtp_use_tls,
                subject_prefix=settings.email_subject_prefix,
                display_timezone=settings.display_timezone,
            )
        )

    async def preview_adapter(self, task_id: int) -> tuple[int, list[tuple[str, str, str]]]:
        task = await asyncio.to_thread(self.tasks.get, task_id)
        if task is None:
            raise KeyError(task_id)
        result = await self.fetcher.fetch(
            FetchRequest(task.id, task.url, task.adapter.encoding, task.adapter.allow_insecure_tls)
        )
        outcome = self.adapters.extract(task, result.raw_content, result.content_type, result.encoding)
        preview = [(item.title, item.url or "", item.published_at.isoformat() if item.published_at else "") for item in outcome.items[:20]]
        return len(outcome.items), preview

    async def run_task(self, task_id: int, *, manual: bool = False) -> RunLog:
        lock = await self._get_task_lock(task_id)
        if lock.locked():
            raise RuntimeError("Task is already running")
        async with lock:
            task = await asyncio.to_thread(self.tasks.get, task_id)
            if task is None:
                raise KeyError(task_id)
            run = await asyncio.to_thread(self.runs.create_running, task_id)
            settings = await self.settings.load()
            interval = task.check_interval_minutes or settings.default_interval_minutes
            jitter = interval * (1 + random.uniform(-0.1, 0.1))
            next_check = utc_now() + timedelta(minutes=jitter)
            await asyncio.to_thread(self.tasks.mark_check_started, task_id)
            try:
                items = await self._collect_items(task)
                keywords = [keyword.casefold() for keyword in task.keywords]
                if keywords:
                    items = [item for item in items if any(keyword in item.title.casefold() for keyword in keywords)]
                change = (
                    await asyncio.to_thread(self.monitoring.detect_list_changes, task, items)
                    if items
                    else None
                )
                if change and not change.baseline_created:
                    await self._check_content_updates(task, items, change.new_items)
                await self._process_pending_events(task.id)
                updated_task = await asyncio.to_thread(
                    self.tasks.mark_check_finished, task, True, None, next_check
                )
                run.status = RunStatus.SUCCESS
                run.finished_at = utc_now()
                await asyncio.to_thread(self.runs.finish, run.id, RunStatus.SUCCESS.value, None)
                if not updated_task.enabled:
                    await self._send_failure_email_safely(updated_task, "\u4efb\u52a1\u8fde\u7eed\u5931\u8d25 10 \u6b21\uff0c\u5df2\u81ea\u52a8\u6682\u505c\uff0c\u8bf7\u68c0\u67e5\u7f51\u7ad9\u662f\u5426\u6539\u7248\u3002")
                return run
            except Exception as exc:
                error = self._safe_error(exc)
                updated_task = await asyncio.to_thread(
                    self.tasks.mark_check_finished, task, False, error, next_check
                )
                run.status = RunStatus.FAILED
                run.finished_at = utc_now()
                run.error_message = error
                await asyncio.to_thread(self.runs.finish, run.id, RunStatus.FAILED.value, error)
                if updated_task.failure_count == 10 and not updated_task.enabled:
                    await self._send_failure_email_safely(updated_task, error)
                return run

    async def _collect_items(self, task: MonitorTask):
        result = await self.fetcher.fetch(
            FetchRequest(task.id, task.url, task.adapter.encoding, task.adapter.allow_insecure_tls)
        )
        snapshot_id = await asyncio.to_thread(
            self.monitoring.save_snapshot, task, result.http_status, result.raw_content, []
        )
        outcome = self.adapters.extract(task, result.raw_content, result.content_type, result.encoding)
        await asyncio.to_thread(
            self.monitoring.update_snapshot_extraction, snapshot_id, outcome.items
        )
        return outcome.items

    async def _check_content_updates(self, task: MonitorTask, items, new_items) -> None:
        if not task.adapter.detect_content_updates:
            return
        new_keys = {item.item_key for item in new_items}
        checked = 0
        for normalized in items:
            if checked >= self.max_update_checks:
                break
            if not normalized.url:
                continue
            if normalized.fingerprint in new_keys:
                continue
            existing = await asyncio.to_thread(self.monitoring.item_by_key, task.id, normalized.fingerprint)
            if existing is None or not existing.url:
                continue
            detail = await self._fetch_detail(task.id, existing.url, task.adapter.encoding, task.adapter.allow_insecure_tls)
            content_hash = hashlib.sha256(detail.text.encode("utf-8")).hexdigest()
            await asyncio.to_thread(self.monitoring.set_item_content_hash, existing, content_hash, True)
            checked += 1

    async def _fetch_detail(self, task_id: int, url: str, encoding: str | None, insecure: bool):
        result = await self.fetcher.fetch(FetchRequest(task_id, url, encoding, insecure))
        return self.extractor.extract(url, result.raw_content, result.encoding, result.content_type)

    async def _process_pending_events(self, task_id: int) -> None:
        settings = await self.settings.load()
        pending = await asyncio.to_thread(self.monitoring.pending_events)
        for event in [item for item in pending if item.task_id == task_id]:
            await self._process_event(event, settings)

    async def process_pending_events(self) -> None:
        settings = await self.settings.load()
        for event in await asyncio.to_thread(self.monitoring.pending_events):
            await self._process_event(event, settings)

    async def _process_event(self, event: ProcessingEvent, settings: AppSettings) -> None:
        if event.status == EventStatus.NOTIFIED:
            return
        task = await asyncio.to_thread(self.tasks.get, event.task_id)
        if task is None:
            return
        if event.status == EventStatus.DETECTED:
            await self._fetch_and_store_event_detail(event, task)
        event = await asyncio.to_thread(self.monitoring.event, event.id)
        if event is None:
            return
        summary_result: SummaryResult | None = None
        if event.status in {EventStatus.FETCHED, EventStatus.SUMMARIZE_FAILED} and task.enable_llm_summary:
            summary_result = await self._summarize_event(event, settings)
            event = await asyncio.to_thread(self.monitoring.event, event.id)
            if event is None:
                return
        if not settings.immediate_email:
            return
        if summary_result is None:
            summary_result = await self._summary_from_record_or_fallback(event)
        await self._notify_event(task, event, summary_result, settings)

    async def _fetch_and_store_event_detail(
        self, event: ProcessingEvent, task: MonitorTask
    ) -> None:
        attachments_json: list[dict[str, str]] = []
        text = ""
        title = event.item_title
        if event.item_url:
            try:
                task_adapter = task.adapter
                detail = await self._fetch_detail(
                    event.task_id,
                    event.item_url,
                    task_adapter.encoding,
                    task_adapter.allow_insecure_tls,
                )
                title = detail.title or title
                text = detail.text
                attachments_json = [
                    {"name": item.name, "url": item.url, "type": item.type} for item in detail.attachments
                ]
                existing = await asyncio.to_thread(self.monitoring.item_by_id, event.item_id)
                if existing is not None:
                    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    await asyncio.to_thread(
                        self.monitoring.set_item_content_hash, existing, content_hash, False
                    )
            except FetchError as exc:
                await asyncio.to_thread(
                    self.monitoring.update_event_status,
                    event.id,
                    EventStatus.DETECTED,
                    f"\u8be6\u60c5\u9875\u62a3\u53d6\u5931\u8d25\uff1a{exc}",
                    True,
                )
                return
        await asyncio.to_thread(self.monitoring.save_event_detail, event.id, title, text, attachments_json)
        await asyncio.to_thread(self.monitoring.update_event_status, event.id, EventStatus.FETCHED)

    async def _summarize_event(self, event: ProcessingEvent, settings: AppSettings) -> SummaryResult | None:
        api_key = await self.settings.llm_api_key()
        summarizer = self._summarizer_factory(settings, api_key)
        try:
            result = await summarizer.summarize(
                event.detail_title or event.item_title, event.detail_text or event.item_title, event.item_url
            )
            await asyncio.to_thread(
                self.monitoring.save_summary,
                event.id,
                settings.llm_model,
                "v1",
                asdict(result),
                result.summary,
            )
            await asyncio.to_thread(self.monitoring.update_event_status, event.id, EventStatus.SUMMARIZED)
            return result
        except SummaryUnavailableError as exc:
            await asyncio.to_thread(
                self.monitoring.update_event_status,
                event.id,
                EventStatus.SUMMARIZE_FAILED,
                f"AI \u6458\u8981\u4e0d\u53ef\u7528\uff1a{exc}",
                True,
            )
            return None

    async def _summary_from_record_or_fallback(self, event: ProcessingEvent) -> SummaryResult:
        record = await asyncio.to_thread(self.monitoring.summary_for_event, event.id)
        if record:
            return SummaryResult(
                relevance=str(record.get("relevance", "medium")),
                category=str(record.get("category", "\u5176\u4ed6")),
                summary=str(record.get("summary", "")),
                key_dates=list(record.get("key_dates", [])),
                action_required=str(record.get("action_required", "")),
                target_audience=str(record.get("target_audience", "")),
                important_links=list(record.get("important_links", [])),
                attachments=list(record.get("attachments", [])),
            )
        try:
            attachments = json.loads(event.detail_attachments_json or "[]")
            names = [str(item.get("name", "")) for item in attachments if isinstance(item, dict)]
            links = [str(item.get("url", "")) for item in attachments if isinstance(item, dict)]
        except json.JSONDecodeError:
            names, links = [], []
        return SummaryResult(
            relevance="medium",
            category=event.event_type == EventType.CONTENT_UPDATED and "\u5185\u5bb9\u66f4\u65b0" or "\u5176\u4ed6",
            summary="AI \u6458\u8981\u6682\u4e0d\u53ef\u7528\uff0c\u8bf7\u901a\u8fc7\u539f\u6587\u94fe\u63a5\u67e5\u770b\u5b8c\u6574\u901a\u77e5\u3002",
            important_links=[event.item_url] if event.item_url else [],
            attachments=names + links,
            degraded=True,
        )

    async def _notify_event(
        self, task: MonitorTask, event: ProcessingEvent, summary: SummaryResult, settings: AppSettings
    ) -> None:
        password = await self.settings.smtp_password()
        notifier = self._notifier_factory(settings, password)
        last_error: str | None = None
        for attempt in range(3):
            try:
                await notifier.send_event(task, event.detail_title or event.item_title, event.item_url, summary)
                await asyncio.to_thread(self.monitoring.update_event_status, event.id, EventStatus.NOTIFIED)
                await asyncio.to_thread(
                    self.monitoring.record_notification, event.id, settings.smtp_recipient, True
                )
                return
            except NotificationError as exc:
                last_error = f"\u90ae\u4ef6\u53d1\u9001\u5931\u8d25\uff1a{exc}"
                if attempt < 2:
                    await asyncio.sleep((attempt + 1) * 0.5)
        await asyncio.to_thread(
            self.monitoring.update_event_status,
            event.id,
            EventStatus.NOTIFY_FAILED,
            last_error,
            True,
        )
        await asyncio.to_thread(
            self.monitoring.record_notification, event.id, settings.smtp_recipient, False, last_error
        )

    async def resend_event(self, event_id: int) -> bool:
        event = await asyncio.to_thread(self.monitoring.event, event_id)
        if event is None:
            raise KeyError(event_id)
        task = await asyncio.to_thread(self.tasks.get, event.task_id)
        if task is None:
            raise KeyError(event.task_id)
        settings = await self.settings.load()
        summary = await self._summary_from_record_or_fallback(event)
        password = await self.settings.smtp_password()
        notifier = self._notifier_factory(settings, password)
        await notifier.send_event(task, event.detail_title or event.item_title, event.item_url, summary)
        await asyncio.to_thread(self.monitoring.update_event_status, event.id, EventStatus.NOTIFIED)
        await asyncio.to_thread(self.monitoring.record_notification, event.id, settings.smtp_recipient, True)
        return True

    async def _send_failure_email_safely(self, task: MonitorTask, error: str) -> None:
        try:
            settings = await self.settings.load()
            password = await self.settings.smtp_password()
            notifier = self._notifier_factory(settings, password)
            await notifier.send_task_failure(task, error)
        except Exception:
            return

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).replace("\n", " ").strip()
        message = re.sub(r"(sk-[A-Za-z0-9_-]+)", "[REDACTED]", message)
        return message[:1000] or type(exc).__name__


class Scheduler:
    def __init__(
        self,
        service: MonitoringService,
        *,
        scan_interval_seconds: float = 30,
        workers: int = 2,
    ):
        self.service = service
        self.scan_interval = scan_interval_seconds
        self.worker_count = workers
        self._queue: asyncio.Queue[tuple[int, int, int]] = asyncio.Queue()
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._sequence = 0
        self._active: set[int] = set()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks.append(asyncio.create_task(self._scanner()))
        for _ in range(self.worker_count):
            self._tasks.append(asyncio.create_task(self._worker()))

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._active.clear()

    def trigger_manual(self, task_id: int) -> None:
        self._sequence += 1
        self._queue.put_nowait((0, self._sequence, task_id))

    async def check_all_enabled(self) -> None:
        tasks = await asyncio.to_thread(self.service.tasks.list)
        for task in tasks:
            if task.enabled:
                self.trigger_manual(task.id)

    async def _scanner(self) -> None:
        while self._running:
            try:
                tasks = await asyncio.to_thread(self.service.tasks.due_tasks, utc_now())
                for task in tasks:
                    if task.id not in self._active:
                        self._sequence += 1
                        self._active.add(task.id)
                        self._queue.put_nowait((1, self._sequence, task.id))
            except Exception:
                # Scheduler failures are non-fatal; the next scan retries.
                pass
            await asyncio.sleep(self.scan_interval)

    async def _worker(self) -> None:
        while self._running:
            priority, _, task_id = await self._queue.get()
            try:
                await self.service.run_task(task_id)
            except Exception:
                pass
            finally:
                self._active.discard(task_id)
                self._queue.task_done()


class AppService:
    def __init__(
        self,
        *,
        data_dir: str | Path | None = None,
        secrets: SecretStore | None = None,
        fetcher: Fetcher | None = None,
        summarizer_factory: SummarizerFactory | None = None,
        notifier_factory: NotifierFactory | None = None,
    ):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "app.db")
        from ..storage.secrets import KeyringSecretStore

        self.secrets = secrets or KeyringSecretStore()
        self.settings_repo = SettingsRepository(self.db)
        self.task_repo = TaskRepository(self.db)
        self.run_repo = RunLogRepository(self.db)
        self.monitor_repo = MonitoringRepository(self.db, self.data_dir)
        self.settings_service = SettingsService(self.settings_repo, self.secrets)
        self.task_service = TaskService(self.task_repo, self.settings_service)
        self.fetcher = fetcher or Fetcher()
        self.monitoring = MonitoringService(
            self.db,
            self.task_repo,
            self.run_repo,
            self.monitor_repo,
            self.fetcher,
            self.settings_service,
            summarizer_factory=summarizer_factory,
            notifier_factory=notifier_factory,
        )
        self.scheduler = Scheduler(self.monitoring)

    async def start(self) -> None:
        await self.scheduler.start()
        await self.monitoring.process_pending_events()

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.fetcher.aclose()

    async def trigger_manual_check(self, task_id: int) -> RunLog:
        return await self.monitoring.run_task(task_id, manual=True)

    async def list_events(self) -> list[ProcessingEvent]:
        return await asyncio.to_thread(self.monitor_repo.events)

    async def recent_runs(self, limit: int = 200) -> list[RunLog]:
        return await asyncio.to_thread(self.run_repo.recent, limit)

    async def test_llm(self) -> str:
        settings = await self.settings_service.load()
        api_key = await self.settings_service.llm_api_key()
        summarizer = self._create_summarizer(settings, api_key)
        result = await summarizer.summarize("\u8fde\u63a5\u6d4b\u8bd5", "\u8fd9\u662f\u4e00\u6761\u8fde\u63a5\u6d4b\u8bd5\u6d88\u606f\u3002", None)
        return result.summary or "LLM \u8fde\u63a5\u6210\u529f"

    def _create_summarizer(self, settings: AppSettings, api_key: str) -> SummarizerProtocol:
        return OpenAICompatibleSummarizer(
            LLMConfig(settings.llm_base_url, api_key, settings.llm_model, settings.llm_timeout_seconds, settings.llm_max_output_tokens)
        )

    async def send_test_email(self) -> None:
        settings = await self.settings_service.load()
        password = await self.settings_service.smtp_password()
        notifier = EmailNotifier(
            SmtpConfig(
                settings.smtp_host,
                settings.smtp_port,
                settings.smtp_username,
                password,
                settings.smtp_sender,
                settings.smtp_recipient,
                settings.smtp_use_tls,
                settings.email_subject_prefix,
                settings.display_timezone,
            )
        )
        await notifier.send_test()

