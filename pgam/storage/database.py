from __future__ import annotations

import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monitor_task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('auto','rss','static_list')),
    adapter_config_json TEXT NOT NULL DEFAULT '{}',
    check_interval_minutes INTEGER NOT NULL DEFAULT 30 CHECK(check_interval_minutes >= 5),
    enabled INTEGER NOT NULL DEFAULT 1,
    last_checked_at TEXT,
    last_success_at TEXT,
    next_check_at TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    enable_llm_summary INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS page_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES monitor_task(id) ON DELETE CASCADE,
    fetched_at TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    extraction_result_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_page_snapshot_task_time
    ON page_snapshot(task_id, fetched_at DESC);
CREATE TABLE IF NOT EXISTS publication_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES monitor_task(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    content_hash TEXT,
    latest_status TEXT NOT NULL DEFAULT 'baseline',
    UNIQUE(task_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_publication_item_task_time
    ON publication_item(task_id, first_seen_at DESC);
CREATE TABLE IF NOT EXISTS processing_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES monitor_task(id) ON DELETE CASCADE,
    item_id INTEGER NOT NULL REFERENCES publication_item(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK(event_type IN ('new_item','content_updated')),
    status TEXT NOT NULL CHECK(status IN ('detected','fetched','summarized','summarize_failed','notified','notify_failed')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    item_content_hash TEXT,
    item_title TEXT NOT NULL DEFAULT '',
    item_url TEXT,
    detail_title TEXT,
    detail_text TEXT,
    detail_attachments_json TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_event_identity
    ON processing_event(item_id, event_type, coalesce(item_content_hash, ''));
CREATE INDEX IF NOT EXISTS idx_processing_event_status
    ON processing_event(status, updated_at);
CREATE TABLE IF NOT EXISTS summary_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES processing_event(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    result_json TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_record (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL REFERENCES processing_event(id) ON DELETE CASCADE,
    channel TEXT NOT NULL DEFAULT 'email',
    recipient TEXT NOT NULL,
    status TEXT NOT NULL,
    sent_at TEXT,
    error_message TEXT
);
CREATE TABLE IF NOT EXISTS run_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES monitor_task(id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running','success','failed')),
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_log_time ON run_log(started_at DESC);
"""


def utc_now() -> datetime:
    return datetime.now(UTC)


def dt_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def dt_from_db(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class Database:
    """Thread-local SQLite facade. WAL is enabled once per new connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self.initialize()

    def _backup_if_upgrade(self) -> None:
        if not self.path.exists():
            return
        with sqlite3.connect(self.path) as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version >= SCHEMA_VERSION:
            return
        backup = self.path.with_name(
            f"{self.path.name}.backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        )
        shutil.copy2(self.path, backup)

    def initialize(self) -> None:
        with self._init_lock:
            self._backup_if_upgrade()
            with self.connect() as conn:
                conn.executescript(SCHEMA)
                self._ensure_migration_columns(conn)
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _ensure_migration_columns(conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(processing_event)")}
        additions = {
            "detail_title": "TEXT",
            "detail_text": "TEXT",
            "detail_attachments_json": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, declaration in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE processing_event ADD COLUMN {name} {declaration}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            self._local.conn = conn
        yield conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError:
                pass
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def query(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def execute(self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(sql, params)
            return cursor.lastrowid or cursor.rowcount

    def close_thread_connection(self) -> None:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
