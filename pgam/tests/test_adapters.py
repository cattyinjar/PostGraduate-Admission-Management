import pytest

from pgam.core.models import EventType, MonitorTask, NormalizedListItem, SourceType
from pgam.sources.adapters import (
    AdapterError,
    SourceAdapterRegistry,
    canonical_url,
    fingerprint_url,
)
from pgam.storage.database import Database, utc_now
from pgam.storage.repositories import MonitoringRepository, TaskRepository


def make_task(tmp_path, source_type=SourceType.AUTO, adapter="{}") -> tuple[Database, MonitorTask]:
    db = Database(tmp_path / "test.db")
    repo = TaskRepository(db)
    now = utc_now()
    return db, repo.create(
        MonitorTask(
            0, "研究生院", "https://grad.example.edu.cn/zs/tzgg.htm", source_type, adapter,
            30, True, None, None, 0, now, now, now,
        )
    )


@pytest.mark.parametrize(
    ("html", "selectors", "expected"),
    [
        (
            """<html><body><ul class="news"> <li><a href='/info/1.htm'>2026年推荐免试研究生招生办法</a><span>2026-09-01</span></li>
            <li><a href='/info/2.htm'>2026年硕士研究生招生简章</a><span>2026-09-02</span></li>
            <li><a href='/info/3.htm'>2026年复试调剂工作办法</a><span>2026-09-03</span></li></ul></body></html>""",
            None,
            3,
        ),
        (
            """<html><body><table><tr><td><a href='news-1.html'>推免生预报名系统开放通知</a></td><td>2026/09/04</td></tr>
            <tr><td><a href='news-2.html'>硕士研究生复试分数线公布</a></td><td>2026/09/05</td></tr>
            <tr><td><a href='news-3.html'>招生咨询活动安排</a></td><td>2026/09/06</td></tr></table></body></html>""",
            None,
            3,
        ),
        (
            """<html><body><ul><li><a href='a.html'>没有日期的通知标题第一条</a></li><li><a href='b.html'>没有日期的通知标题第二条</a></li><li><a href='c.html'>没有日期的通知标题第三条</a></li></ul></body></html>""",
            None,
            3,
        ),
        (
            """<html><body><div class='list'><div class='row'><a class='t' href='1.html'>夏令营报名通知</a><span>2026.07.01</span></div>
            <div class='row'><a class='t' href='2.html'>预推免报名通知</a><span>2026.07.02</span></div></div></body></html>""",
            """{"item_selector":".row","link_selector":"a.t"}""",
            2,
        ),
    ],
    ids=["ul-list", "table-list", "no-date", "selected"],
)
def test_static_auto_and_selected(tmp_path, html, selectors, expected):
    _, task = make_task(tmp_path, adapter=selectors or "{}")
    registry = SourceAdapterRegistry()
    result = registry.extract(task, html.encode("utf-8"), "text/html; charset=utf-8", "utf-8")
    assert len(result.items) == expected
    assert all(item.url.startswith("https://grad.example.edu.cn/") for item in result.items)


def test_static_gbk_encoding(tmp_path):
    _, task = make_task(tmp_path)
    html = """<html><body><ul><li><a href='1.html'>推荐免试研究生报名通知</a></li><li><a href='2.html'>硕士研究生复试通知</a></li><li><a href='3.html'>招生调剂工作通知</a></li></ul></body></html>"""
    registry = SourceAdapterRegistry()
    result = registry.extract(task, html.encode("gbk"), "text/html", "")
    assert len(result.items) == 3


def test_invalid_static_page_raises_structured_error(tmp_path):
    _, task = make_task(tmp_path)
    with pytest.raises(AdapterError):
        SourceAdapterRegistry().extract(task, b"<html><body><p>no list</p></body></html>", "text/html", "")


def test_rss(tmp_path):
    rss = """<?xml version='1.0'?><rss version='2.0'><channel><title>Admissions</title>
    <item><title>推免通知</title><link>https://grad.example.edu.cn/1.html?utm_source=x</link><pubDate>Wed, 17 Sep 2026 08:00:00 +0000</pubDate></item>
    <item><title>考研复试通知</title><link>https://grad.example.edu.cn/2.html</link></item></channel></rss>"""
    _, task = make_task(tmp_path, SourceType.RSS)
    result = SourceAdapterRegistry().extract(task, rss.encode(), "application/rss+xml", "")
    assert len(result.items) == 2
    assert result.items[0].published_at is not None


def test_url_normalization_and_fingerprint():
    canonical = canonical_url("HTTPS://Example.COM/a?utm_source=x&id=1#top")
    assert canonical == "https://example.com/a?id=1"
    assert fingerprint_url("https://example.com/a?utm_source=x") == fingerprint_url("https://example.com/a")


def test_first_run_creates_baseline_and_second_run_creates_event(tmp_path):
    db, task = make_task(tmp_path)
    repo = MonitoringRepository(db, tmp_path)
    first = NormalizedListItem("旧通知", "https://grad.example.edu.cn/old.html", None, fingerprint_url("https://grad.example.edu.cn/old.html"))
    assert repo.detect_list_changes(task, [first]).baseline_created
    assert repo.pending_events() == []
    new = NormalizedListItem("新通知", "https://grad.example.edu.cn/new.html", None, fingerprint_url("https://grad.example.edu.cn/new.html"))
    result = repo.detect_list_changes(task, [first, new])
    assert not result.baseline_created
    assert len(result.new_items) == 1
    events = repo.pending_events()
    assert len(events) == 1
    assert events[0].event_type == EventType.NEW_ITEM
