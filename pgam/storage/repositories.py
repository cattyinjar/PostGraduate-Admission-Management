from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.models import (
    AdapterConfig,
    AppSettings,
    ChangeResult,
    EventStatus,
    EventType,
    MonitorTask,
    MonitorTaskView,
    NormalizedListItem,
    ProcessingEvent,
    PublicationItem,
    RunLog,
    RunStatus,
    SourceType,
)
from .database import Database, dt_from_db, dt_to_db, utc_now


def _task_from_row(row: sqlite3.Row) -> MonitorTask:
    return MonitorTask(
        id=int(row["id"]),
        name=str(row["name"]),
        url=str(row["url"]),
        source_type=SourceType(str(row["source_type"])),
        adapter_config_json=str(row["adapter_config_json"]),
        check_interval_minutes=int(row["check_interval_minutes"]),
        enabled=bool(row["enabled"]),
        last_checked_at=dt_from_db(row["last_checked_at"]),
        last_success_at=dt_from_db(row["last_success_at"]),
        failure_count=int(row["failure_count"]),
        created_at=dt_from_db_db(row["created_at"]),
        updated_at=dt_from_db_db(row["updated_at"]),
        next_check_at=dt_from_db(row["next_check_at"]),
        keywords_json=str(row["keywords_json"]),
        enable_llm_summary=bool(row["enable_llm_summary"]),
    )


def dt_from_db_db(value: str) -> datetime:
    result = dt_from_db(value)
    if result is None:
        raise ValueError("Created/updated timestamp cannot be null")
    return result


def _item_from_row(row: sqlite3.Row) -> PublicationItem:
    return PublicationItem(
        id=int(row["id"]),
        task_id=int(row["task_id"]),
        item_key=str(row["item_key"]),
        title=str(row["title"]),
        url=row["url"],
        published_at=dt_from_db(row["published_at"]),
        first_seen_at=dt_from_db_db(row["first_seen_at"]),
        content_hash=row["content_hash"],
        latest_status=str(row["latest_status"]),
    )


def _event_from_row(row: sqlite3.Row) -> ProcessingEvent:
    return ProcessingEvent(
        id=int(row["id"]),
        task_id=int(row["task_id"]),
        item_id=int(row["item_id"]),
        event_type=EventType(row["event_type"]),
        status=EventStatus(row["status"]),
        retry_count=int(row["retry_count"]),
        last_error=row["last_error"],
        created_at=dt_from_db_db(row["created_at"]),
        updated_at=dt_from_db_db(row["updated_at"]),
        item_content_hash=row["item_content_hash"],
        item_title=row["item_title"] or row["fallback_title"] or "",
        item_url=row["item_url"] or row["fallback_url"],
        task_name=(
            row["task_name"] if "task_name" in row.keys() else row["fallback_task_name"]
        ),
        detail_title=row["detail_title"] if "detail_title" in row.keys() else None,
        detail_text=row["detail_text"] if "detail_text" in row.keys() else None,
        detail_attachments_json=row["detail_attachments_json"] if "detail_attachments_json" in row.keys() else "[]",
    )


def _run_from_row(row: sqlite3.Row) -> RunLog:
    return RunLog(
        id=int(row["id"]),
        task_id=int(row["task_id"]),
        started_at=dt_from_db_db(row["started_at"]),
        finished_at=dt_from_db(row["finished_at"]),
        status=RunStatus(row["status"]),
        error_message=row["error_message"],
    )


class TaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, task: MonitorTask) -> MonitorTask:
        now = dt_to_db(utc_now()) or ""
        interval = max(5, task.check_interval_minutes)
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO monitor_task(
                    name,url,source_type,adapter_config_json,check_interval_minutes,enabled,
                    last_checked_at,last_success_at,next_check_at,failure_count,created_at,updated_at,
                    keywords_json,enable_llm_summary
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    task.name,
                    task.url,
                    task.source_type.value,
                    task.adapter_config_json or "{}",
                    interval,
                    int(task.enabled),
                    dt_to_db(task.last_checked_at),
                    dt_to_db(task.last_success_at),
                    dt_to_db(task.next_check_at),
                    task.failure_count,
                    now,
                    now,
                    task.keywords_json or "[]",
                    int(task.enable_llm_summary),
                ),
            )
            task_id = int(cursor.lastrowid)
        result = self.get(task_id)
        if result is None:
            raise RuntimeError("Task disappeared after insert")
        return result

    def update(self, task: MonitorTask) -> MonitorTask:
        now = dt_to_db(utc_now())
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE monitor_task SET name=?,url=?,source_type=?,adapter_config_json=?,
                    check_interval_minutes=?,enabled=?,enable_llm_summary=?,keywords_json=?,next_check_at=?,updated_at=?
                WHERE id=?
                """,
                (
                    task.name,
                    task.url,
                    task.source_type.value,
                    task.adapter_config_json or "{}",
                    max(5, task.check_interval_minutes),
                    int(task.enabled),
                    int(task.enable_llm_summary),
                    task.keywords_json or "[]",
                    dt_to_db(task.next_check_at),
                    now,
                    task.id,
                ),
            )
        result = self.get(task.id)
        if result is None:
            raise KeyError(f"Task {task.id} not found")
        return result

    def delete(self, task_id: int) -> None:
        self.db.execute("DELETE FROM monitor_task WHERE id=?", (task_id,))

    def get(self, task_id: int) -> MonitorTask | None:
        rows = self.db.query("SELECT * FROM monitor_task WHERE id=?", (task_id,))
        return _task_from_row(rows[0]) if rows else None

    def list(self) -> list[MonitorTask]:
        rows = self.db.query("SELECT * FROM monitor_task ORDER BY created_at DESC, id DESC")
        return [_task_from_row(row) for row in rows]

    def due_tasks(self, now: datetime) -> list[MonitorTask]:
        rows = self.db.query(
            """
            SELECT * FROM monitor_task
            WHERE enabled=1 AND (next_check_at IS NULL OR next_check_at<=?)
            ORDER BY coalesce(next_check_at, created_at), id
            """,
            (dt_to_db(now),),
        )
        return [_task_from_row(row) for row in rows]

    def mark_check_started(self, task_id: int) -> None:
        self.db.execute(
            "UPDATE monitor_task SET last_checked_at=?, updated_at=? WHERE id=?",
            (dt_to_db(utc_now()), dt_to_db(utc_now()), task_id),
        )

    def mark_check_finished(
        self,
        task: MonitorTask,
        success: bool,
        error: str | None,
        next_check_at: datetime | None,
    ) -> MonitorTask:
        now = dt_to_db(utc_now())
        failure_count = 0 if success else task.failure_count + 1
        enabled = task.enabled and failure_count < 10
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE monitor_task SET last_success_at=?,failure_count=?,enabled=?,next_check_at=?,updated_at=?
                WHERE id=?
                """,
                (
                    now if success else dt_to_db(task.last_success_at),
                    failure_count,
                    int(enabled),
                    dt_to_db(next_check_at),
                    now,
                    task.id,
                ),
            )
        result = self.get(task.id)
        if result is None:
            raise KeyError(task.id)
        return result

    def views(self) -> list[MonitorTaskView]:
        tasks = self.list()
        result: list[MonitorTaskView] = []
        for task in tasks:
            counts = self.db.query(
                """
                SELECT
                  (SELECT count(*) FROM publication_item WHERE task_id=?) AS item_count,
                  (SELECT count(*) FROM processing_event WHERE task_id=?
                     AND status NOT IN ('notified')) AS pending_count
                """,
                (task.id, task.id),
            )[0]
            status = "paused"
            if task.enabled:
                status = "error" if task.failure_count >= 3 else "running"
            result.append(
                MonitorTaskView(
                    **{
                        **asdict(task),
                        "current_status": status,
                        "item_count": int(counts["item_count"]),
                        "pending_event_count": int(counts["pending_count"]),
                    }
                )
            )
        return result


class SettingsRepository:
    _defaults = AppSettings()

    def __init__(self, db: Database):
        self.db = db

    def load(self) -> AppSettings:
        rows = self.db.query("SELECT key,value FROM app_config")
        values = {row["key"]: row["value"] for row in rows}
        data = asdict(self._defaults)
        for key in data:
            if key not in values:
                continue
            raw = values[key]
            current = data[key]
            if isinstance(current, bool):
                data[key] = raw.lower() in {"1", "true", "yes"}
            elif isinstance(current, int):
                data[key] = int(raw)
            else:
                data[key] = raw
        return AppSettings(**data)

    def save(self, settings: AppSettings) -> None:
        data = asdict(settings)
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO app_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(key, str(value)) for key, value in data.items()],
            )


class RunLogRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_running(self, task_id: int) -> RunLog:
        run_id = self.db.execute(
            "INSERT INTO run_log(task_id,started_at,status) VALUES (?,?,?)",
            (task_id, dt_to_db(utc_now()), RunStatus.RUNNING.value),
        )
        return RunLog(run_id, task_id, utc_now(), None, RunStatus.RUNNING, None)

    def finish(self, run_id: int, status: str, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE run_log SET finished_at=?,status=?,error_message=? WHERE id=?",
            (dt_to_db(utc_now()), status, error, run_id),
        )

    def recent(self, limit: int = 200) -> list[RunLog]:
        rows = self.db.query(
            "SELECT * FROM run_log ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [_run_from_row(row) for row in rows]


class MonitoringRepository:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.data_dir = data_dir
        self.snapshot_dir = data_dir / "snapshots"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def save_snapshot(
        self,
        task: MonitorTask,
        http_status: int,
        content: bytes,
        items: list[NormalizedListItem],
    ) -> int:
        content_hash = hashlib.sha256(content).hexdigest()
        timestamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        path = self.snapshot_dir / f"task-{task.id}-{timestamp}.bin"
        path.write_bytes(content)
        payload = json.dumps([asdict(item) for item in items], ensure_ascii=False, default=str)
        return self.db.execute(
            """
            INSERT INTO page_snapshot(task_id,fetched_at,http_status,content_hash,storage_path,extraction_result_json)
            VALUES (?,?,?,?,?,?)
            """,
            (task.id, dt_to_db(utc_now()), http_status, content_hash, str(path), payload),
        )

    def update_snapshot_extraction(self, snapshot_id: int, items: list[NormalizedListItem]) -> None:
        payload = json.dumps([asdict(item) for item in items], ensure_ascii=False, default=str)
        self.db.execute(
            "UPDATE page_snapshot SET extraction_result_json=? WHERE id=?",
            (payload, snapshot_id),
        )

    def detect_list_changes(self, task: MonitorTask, items: list[NormalizedListItem]) -> ChangeResult:
        now = dt_to_db(utc_now())
        with self.db.transaction() as conn:
            count = int(conn.execute("SELECT count(*) FROM publication_item WHERE task_id=?", (task.id,)).fetchone()[0])
            if count == 0:
                for item in items:
                    conn.execute(
                        """
                        INSERT INTO publication_item(task_id,item_key,title,url,published_at,first_seen_at,latest_status)
                        VALUES (?,?,?,?,?,?,'baseline') ON CONFLICT(task_id,item_key) DO NOTHING
                        """,
                        (task.id, item.fingerprint, item.title, item.url, dt_to_db(item.published_at), now),
                    )
                return ChangeResult(baseline_created=True, new_items=[], updated_items=[])

            existing_rows = conn.execute(
                "SELECT * FROM publication_item WHERE task_id=?", (task.id,)
            ).fetchall()
            existing = {row["item_key"]: _item_from_row(row) for row in items and existing_rows}
            new_items: list[PublicationItem] = []
            for item in items:
                current = existing.get(item.fingerprint)
                if current is None:
                    cursor = conn.execute(
                        """
                        INSERT INTO publication_item(task_id,item_key,title,url,published_at,first_seen_at,latest_status)
                        VALUES (?,?,?,?,?,?,'detected')
                        """,
                        (task.id, item.fingerprint, item.title, item.url, dt_to_db(item.published_at), now),
                    )
                    publication_id = int(cursor.lastrowid)
                    conn.execute(
                        """
                        INSERT INTO processing_event(
                            task_id,item_id,event_type,status,created_at,updated_at,item_content_hash,item_title,item_url
                        ) VALUES (?,?,'new_item','detected',?,?,?,?,?)
                        ON CONFLICT DO NOTHING
                        """,
                        (task.id, publication_id, now, now, item.fingerprint, item.title, item.url),
                    )
                    new_items.append(
                        PublicationItem(
                            publication_id, task.id, item.fingerprint, item.title, item.url,
                            item.published_at, utc_now(), None, "detected"
                        )
                    )
                else:
                    conn.execute(
                        "UPDATE publication_item SET title=?,url=?,published_at=? WHERE id=?",
                        (item.title, item.url, dt_to_db(item.published_at), current.id),
                    )
        return ChangeResult(baseline_created=False, new_items=new_items, updated_items=[])

    def existing_items_for_task(self, task_id: int) -> list[PublicationItem]:
        rows = self.db.query(
            "SELECT * FROM publication_item WHERE task_id=? ORDER BY first_seen_at DESC", (task_id,)
        )
        return [_item_from_row(row) for row in rows]

    def item_by_id(self, item_id: int) -> PublicationItem | None:
        rows = self.db.query("SELECT * FROM publication_item WHERE id=?", (item_id,))
        return _item_from_row(rows[0]) if rows else None

    def item_by_key(self, task_id: int, item_key: str) -> PublicationItem | None:
        rows = self.db.query(
            "SELECT * FROM publication_item WHERE task_id=? AND item_key=?", (task_id, item_key)
        )
        return _item_from_row(rows[0]) if rows else None

    def set_item_content_hash(
        self, item: PublicationItem, content_hash: str, create_update_event: bool = True
    ) -> ProcessingEvent | None:
        now = dt_to_db(utc_now())
        changed = item.content_hash is not None and item.content_hash != content_hash
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE publication_item SET content_hash=?,latest_status=? WHERE id=?",
                (content_hash, "updated" if changed else item.latest_status, item.id),
            )
            if not create_update_event or not changed:
                return None
            cursor = conn.execute(
                """
                INSERT INTO processing_event(
                    task_id,item_id,event_type,status,created_at,updated_at,item_content_hash,item_title,item_url
                ) VALUES (?,?,'content_updated','detected',?,?,?,?,?)
                ON CONFLICT DO NOTHING
                """,
                (
                    item.task_id, item.id, now, now, content_hash,
                    item.title, item.url,
                ),
            )
            event_id = int(cursor.lastrowid)
            row = conn.execute(
                """
                SELECT p.*, i.title AS fallback_title, i.url AS fallback_url, t.name AS fallback_task_name
                FROM processing_event p
                JOIN publication_item i ON i.id=p.item_id
                JOIN monitor_task t ON t.id=p.task_id
                WHERE p.id=?
                """,
                (event_id,),
            ).fetchone()
            return _event_from_row(row) if row else None

    def pending_events(self) -> list[ProcessingEvent]:
        return self.events(statuses=["detected", "fetched", "summarized", "summarize_failed", "notify_failed"])

    def events(self, statuses: list[str] | None = None) -> list[ProcessingEvent]:
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            rows = self.db.query(
                f"""
                SELECT p.*, i.title AS fallback_title, i.url AS fallback_url, t.name AS fallback_task_name
                FROM processing_event p
                JOIN publication_item i ON i.id=p.item_id
                JOIN monitor_task t ON t.id=p.task_id
                WHERE p.status IN ({placeholders})
                ORDER BY p.created_at
                """,
                tuple(statuses),
            )
        else:
            rows = self.db.query(
                """
                SELECT p.*, i.title AS fallback_title, i.url AS fallback_url, t.name AS fallback_task_name
                FROM processing_event p
                JOIN publication_item i ON i.id=p.item_id
                JOIN monitor_task t ON t.id=p.task_id
                ORDER BY p.created_at DESC
                """
            )
        return [_event_from_row(row) for row in rows]

    def event(self, event_id: int) -> ProcessingEvent | None:
        rows = self.db.query(
            """
            SELECT p.*, i.title AS fallback_title, i.url AS fallback_url, t.name AS fallback_task_name
            FROM processing_event p JOIN publication_item i ON i.id=p.item_id
            JOIN monitor_task t ON t.id=p.task_id WHERE p.id=?
            """,
            (event_id,),
        )
        return _event_from_row(rows[0]) if rows else None

    def save_event_detail(
        self, event_id: int, title: str, text: str, attachments: list[dict[str, str]]
    ) -> None:
        self.db.execute(
            """
            UPDATE processing_event
            SET detail_title=?, detail_text=?, detail_attachments_json=?, updated_at=?
            WHERE id=?
            """,
            (title, text, json.dumps(attachments, ensure_ascii=False), dt_to_db(utc_now()), event_id),
        )

    def update_event_status(
        self, event_id: int, status: EventStatus, error: str | None = None, increment_retry: bool = False
    ) -> None:
        self.db.execute(
            """
            UPDATE processing_event SET status=?,last_error=?,updated_at=?,
                retry_count=retry_count+? WHERE id=?
            """,
            (status.value, error, dt_to_db(utc_now()), int(increment_retry), event_id),
        )

    def save_summary(
        self, event_id: int, model: str, prompt_version: str, result: dict[str, Any], summary_text: str
    ) -> None:
        self.db.execute(
            """
            INSERT INTO summary_record(event_id,model,prompt_version,result_json,summary_text,created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                event_id,
                model,
                prompt_version,
                json.dumps(result, ensure_ascii=False),
                summary_text,
                dt_to_db(utc_now()),
            ),
        )

    def summary_for_event(self, event_id: int) -> dict[str, Any] | None:
        rows = self.db.query(
            "SELECT result_json FROM summary_record WHERE event_id=? ORDER BY id DESC LIMIT 1",
            (event_id,),
        )
        if not rows:
            return None
        value = json.loads(rows[0]["result_json"])
        return value if isinstance(value, dict) else None

    def record_notification(
        self, event_id: int, recipient: str, success: bool, error: str | None = None
    ) -> None:
        self.db.execute(
            """
            INSERT INTO notification_record(event_id,channel,recipient,status,sent_at,error_message)
            VALUES (?, 'email', ?, ?, ?, ?)
            """,
            (
                event_id,
                recipient,
                "success" if success else "failed",
                dt_to_db(utc_now()) if success else None,
                error,
            ),
        )

    def adapter_config(self, task: MonitorTask) -> AdapterConfig:
        return task.adapter

