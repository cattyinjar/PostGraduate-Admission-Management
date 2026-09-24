from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SourceType(StrEnum):
    AUTO = "auto"
    RSS = "rss"
    STATIC_LIST = "static_list"


class EventType(StrEnum):
    NEW_ITEM = "new_item"
    CONTENT_UPDATED = "content_updated"


class EventStatus(StrEnum):
    DETECTED = "detected"
    FETCHED = "fetched"
    SUMMARIZED = "summarized"
    SUMMARIZE_FAILED = "summarize_failed"
    NOTIFIED = "notified"
    NOTIFY_FAILED = "notify_failed"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(slots=True)
class AdapterConfig:
    item_selector: str | None = None
    title_selector: str | None = None
    link_selector: str | None = None
    date_selector: str | None = None
    encoding: str | None = None
    allow_insecure_tls: bool = False
    detect_content_updates: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> AdapterConfig:
        allowed = {key: item for key, item in (value or {}).items() if key in cls.__dataclass_fields__}
        return cls(**allowed)


@dataclass(slots=True)
class TaskCreateInput:
    name: str
    url: str
    source_type: SourceType = SourceType.AUTO
    check_interval_minutes: int = 30
    enabled: bool = True
    keywords: list[str] = field(default_factory=list)
    enable_llm_summary: bool = True
    adapter: AdapterConfig = field(default_factory=AdapterConfig)


@dataclass(slots=True)
class TaskUpdateInput:
    name: str
    url: str
    source_type: SourceType
    check_interval_minutes: int
    enabled: bool
    keywords: list[str]
    enable_llm_summary: bool
    adapter: AdapterConfig


@dataclass(slots=True)
class MonitorTask:
    id: int
    name: str
    url: str
    source_type: SourceType
    adapter_config_json: str
    check_interval_minutes: int
    enabled: bool
    last_checked_at: datetime | None
    last_success_at: datetime | None
    failure_count: int
    created_at: datetime
    updated_at: datetime
    next_check_at: datetime | None = None
    keywords_json: str = "[]"
    enable_llm_summary: bool = True

    @property
    def keywords(self) -> list[str]:
        import json

        value = json.loads(self.keywords_json or "[]")
        return value if isinstance(value, list) else []

    @property
    def adapter(self) -> AdapterConfig:
        import json

        return AdapterConfig.from_dict(json.loads(self.adapter_config_json or "{}"))


@dataclass(slots=True)
class MonitorTaskView(MonitorTask):
    current_status: str = "idle"
    item_count: int = 0
    pending_event_count: int = 0


@dataclass(slots=True)
class RunLog:
    id: int
    task_id: int
    started_at: datetime
    finished_at: datetime | None
    status: RunStatus
    error_message: str | None


@dataclass(slots=True)
class NormalizedListItem:
    title: str
    url: str | None
    published_at: datetime | None
    fingerprint: str


@dataclass(slots=True)
class PublicationItem:
    id: int
    task_id: int
    item_key: str
    title: str
    url: str | None
    published_at: datetime | None
    first_seen_at: datetime
    content_hash: str | None
    latest_status: str


@dataclass(slots=True)
class ProcessingEvent:
    id: int
    task_id: int
    item_id: int
    event_type: EventType
    status: EventStatus
    retry_count: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    item_content_hash: str | None = None
    item_title: str = ""
    item_url: str | None = None
    task_name: str = ""
    detail_title: str | None = None
    detail_text: str | None = None
    detail_attachments_json: str = "[]"


@dataclass(slots=True)
class ChangeResult:
    baseline_created: bool
    new_items: list[PublicationItem]
    updated_items: list[PublicationItem]
    ignored_items: list[PublicationItem] = field(default_factory=list)


@dataclass(slots=True)
class SummaryResult:
    relevance: str
    category: str
    summary: str
    key_dates: list[dict[str, str]] = field(default_factory=list)
    action_required: str = ""
    target_audience: str = ""
    important_links: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    degraded: bool = False


@dataclass(slots=True)
class AppSettings:
    default_interval_minutes: int = 30
    auto_start_enabled: bool = True
    email_subject_prefix: str = "【招生监视】"
    immediate_email: bool = True
    llm_base_url: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_output_tokens: int = 1600
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_sender: str = ""
    smtp_recipient: str = ""
    smtp_use_tls: bool = True
