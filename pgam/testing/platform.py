from __future__ import annotations

import html
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse


@dataclass(slots=True)
class TestAnnouncement:
    id: int
    title: str
    body: str
    attachment_name: str | None = None
    published_at: datetime | None = None


class LocalAdmissionSite:
    """A small real HTTP site used by integration tests."""

    def __init__(self, name: str, kind: str, initial_titles: list[str]):
        if kind not in {"static_ul", "table_gbk", "rss"}:
            raise ValueError(f"Unsupported local site kind: {kind}")
        self.name = name
        self.kind = kind
        self.slug = {
            "static_ul": "site-a",
            "table_gbk": "site-b",
            "rss": "site-c",
        }[kind]
        self._lock = threading.RLock()
        self._next_id = 1
        self.decorative_footer = "Original local test footer"
        self.announcements: list[TestAnnouncement] = []
        for title in initial_titles:
            self._append_locked(title, "Initial local admission announcement body.")

        server_class = _make_handler_class(self)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), server_class)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.list_url = f"{self.base_url}/{self.slug}/list.htm" if kind != "rss" else f"{self.base_url}/feed.xml"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"pgam-local-site-{self.slug}",
            daemon=True,
        )
        self._started = False

    def __enter__(self) -> LocalAdmissionSite:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.thread.start()

    def close(self) -> None:
        if self._started:
            self.server.shutdown()
        self.server.server_close()
        if self._started:
            self.thread.join(timeout=5)
        self._started = False

    def publish(
        self,
        title: str,
        body: str,
        *,
        attachment_name: str | None = None,
    ) -> TestAnnouncement:
        with self._lock:
            return self._append_locked(title, body, attachment_name)

    def change_decoration(self, text: str) -> None:
        with self._lock:
            self.decorative_footer = text

    def _append_locked(
        self, title: str, body: str, attachment_name: str | None = None
    ) -> TestAnnouncement:
        announcement = TestAnnouncement(
            id=self._next_id,
            title=title,
            body=body,
            attachment_name=attachment_name,
            published_at=datetime.now(UTC),
        )
        self._next_id += 1
        self.announcements.append(announcement)
        return announcement

    def _get_announcement(self, announcement_id: int) -> TestAnnouncement | None:
        with self._lock:
            return next((item for item in self.announcements if item.id == announcement_id), None)

    def _render_list(self) -> tuple[bytes, str]:
        with self._lock:
            announcements = list(reversed(self.announcements))
            footer = self.decorative_footer
        dates = {
            announcement.id: f"2026-09-{announcement.id:02d}"
            for announcement in announcements
        }
        if self.kind == "static_ul":
            rows = "\n".join(
                f'<li><a href="/{self.slug}/news/{item.id}.htm">{html.escape(item.title)}</a>'
                f"<span>{dates[item.id]}</span></li>"
                for item in announcements
            )
            document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Site A admission notices</title></head>
<body><h1>Site A admissions</h1><ul class="news">{rows}</ul>
<footer>{html.escape(footer)}</footer></body></html>"""
            return document.encode("utf-8"), "text/html; charset=utf-8"

        rows = "\n".join(
            f'<tr><td><a href="/{self.slug}/news/{item.id}.htm">{html.escape(item.title)}</a></td>'
            f"<td>{dates[item.id]}</td></tr>"
            for item in announcements
        )
        document = f"""<!doctype html>
<html><head><meta charset="gbk"><title>Site B admission notices</title></head>
<body><h1>Site B admissions</h1><table class="news-list"><tbody>{rows}</tbody></table>
<footer>{html.escape(footer)}</footer></body></html>"""
        return document.encode("gbk"), "text/html; charset=gbk"

    def _render_rss(self) -> tuple[bytes, str]:
        with self._lock:
            announcements = list(reversed(self.announcements))
        items = "\n".join(
            f"""<item>
  <title>{html.escape(item.title)}</title>
  <link>{self.base_url}/{self.slug}/news/{item.id}.htm</link>
  <guid>{self.base_url}/{self.slug}/news/{item.id}.htm</guid>
  <pubDate>{item.published_at.strftime("%a, %d %b %Y %H:%M:%S +0000") if item.published_at else ""}</pubDate>
</item>"""
            for item in announcements
        )
        document = f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Site C admission feed</title>
<link>{self.base_url}/</link><description>Local admission test feed</description>
{items}</channel></rss>"""
        return document.encode("utf-8"), "application/rss+xml; charset=utf-8"

    def _render_detail(self, announcement: TestAnnouncement) -> tuple[bytes, str]:
        attachment = ""
        if announcement.attachment_name:
            attachment = (
                f'<p><a href="/{self.slug}/files/{announcement.id}.pdf">'
                f"{html.escape(announcement.attachment_name)}</a></p>"
            )
        document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{html.escape(announcement.title)}</title></head>
<body><article><h1>{html.escape(announcement.title)}</h1>
<p>{html.escape(announcement.body)}</p>{attachment}</article>
<footer>{html.escape(self.decorative_footer)}</footer></body></html>"""
        return document.encode("utf-8"), "text/html; charset=utf-8"


def _make_handler_class(site: LocalAdmissionSite) -> type[BaseHTTPRequestHandler]:
    class LocalSiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = unquote(urlparse(self.path).path)
            if path == f"/{site.slug}/list.htm" and site.kind != "rss":
                content, content_type = site._render_list()
            elif path == "/feed.xml" and site.kind == "rss":
                content, content_type = site._render_rss()
            elif path.startswith(f"/{site.slug}/news/") and path.endswith(".htm"):
                try:
                    announcement_id = int(path.removesuffix(".htm").rsplit("/", 1)[1])
                except ValueError:
                    self._send(404, b"Not found", "text/plain; charset=utf-8")
                    return
                announcement = site._get_announcement(announcement_id)
                if announcement is None:
                    self._send(404, b"Announcement not found", "text/plain; charset=utf-8")
                    return
                content, content_type = site._render_detail(announcement)
            elif (
                site.kind == "table_gbk"
                and path.startswith("/site-b/files/")
                and path.endswith(".pdf")
            ):
                content, content_type = b"%PDF-1.4 local test attachment\n", "application/pdf"
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            self._send(200, content, content_type)

        def _send(self, status: int, content: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: object) -> None:
            return

    return LocalSiteHandler


class LocalAdmissionPlatform:
    """Three independent real localhost sites covering static HTML, GBK tables, and RSS."""

    def __init__(self) -> None:
        self.site_a = LocalAdmissionSite(
            "site_a_static_ul",
            "static_ul",
            [
                "Site A initial admission notice 1",
                "Site A initial admission notice 2",
                "Site A initial admission notice 3",
            ],
        )
        self.site_b = LocalAdmissionSite(
            "site_b_table_gbk",
            "table_gbk",
            [
                "Site B \u521d\u59cb\u62db\u751f\u901a\u77e5\u4e00",
                "Site B \u521d\u59cb\u62db\u751f\u901a\u77e5\u4e00",
                "Site B \u521d\u59cb\u62db\u751f\u901a\u77e5\u4e00",
            ],
        )
        self.site_c = LocalAdmissionSite(
            "site_c_rss",
            "rss",
            [
                "Site C initial admission notice 1",
                "Site C initial admission notice 2",
                "Site C initial admission notice 3",
            ],
        )
        self.sites = [self.site_a, self.site_b, self.site_c]

    def __enter__(self) -> LocalAdmissionPlatform:
        for site in self.sites:
            site.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        for site in self.sites:
            site.close()


if __name__ == "__main__":
    with LocalAdmissionPlatform() as platform:
        for site in platform.sites:
            print(site.name, site.list_url)
