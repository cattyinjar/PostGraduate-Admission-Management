from __future__ import annotations

import html
import threading
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse


@dataclass(frozen=True, slots=True)
class RealisticSiteProfile:
    key: str
    list_path: str
    layout: str
    encoding: str = "utf-8"


REALISTIC_SITE_ORIGINS: dict[str, str] = {
    "ia_cas": "https://ia.cas.cn/yjsjy/zs/sszs/",
    "tsinghua_yzbm": "https://yzbm.tsinghua.edu.cn/publish/s03/s0301/list?yxsdm=045",
    "tsinghua_life": "https://life.tsinghua.edu.cn/rcpy/yjsjy/zsxxgk1.htm",
    "tsinghua_au": "https://www.au.tsinghua.edu.cn/zsjy/yjszs.htm",
    "sjtu": "https://yzb.sjtu.edu.cn/zkxx/sszs",
    "nju": "https://yzb.nju.edu.cn/47865/list.htm",
    "fudan": "https://gsao.fudan.edu.cn/15029/list.htm",
    "ustc": "https://yz.ustc.edu.cn/column/182?num=-1",
    "amss": "https://amss.cas.cn/jypy/zxxx/",
    "ict": "https://ict.cas.cn/yjsjy/zsxx/sszs/",
    "is_cas": "https://is.cas.cn/yjsjy/zsxx/",
    "ipe": "http://edu.ipe.ac.cn/zsxx/",
    "sia": "https://sia.cas.cn/zpjy/yjsjy/",
    "iphy": "https://edu.iphy.ac.cn/?q=list2&id=3277",
    "ime": "https://ime.cas.cn/kjrh/tzggkjrh/",
    "semi": "https://bdt.semi.ac.cn/yanjiusheng/channels/691.html",
}


@dataclass(slots=True)
class SimulatedAnnouncement:
    id: int
    token: str
    title: str
    body: str
    published_at: datetime


REALISTIC_SITE_PROFILES: tuple[RealisticSiteProfile, ...] = (
    RealisticSiteProfile("ia_cas", "/yjsjy/zs/sszs/", "ia_cas"),
    RealisticSiteProfile("tsinghua_yzbm", "/publish/s03/s0301/list?yxsdm=045", "pseudo_link"),
    RealisticSiteProfile("tsinghua_life", "/rcpy/yjsjy/zsxxgk1.htm", "tsinghua_life"),
    RealisticSiteProfile("tsinghua_au", "/zsjy/yjszs.htm", "card_calendar"),
    RealisticSiteProfile("sjtu", "/zkxx/sszs", "calendar_item"),
    RealisticSiteProfile("nju", "/47865/list.htm", "title_meta_spans"),
    RealisticSiteProfile("fudan", "/15029/list.htm", "fudan_with_directory_distractor"),
    RealisticSiteProfile("ustc", "/column/182?num=-1", "onclick_rows"),
    RealisticSiteProfile("amss", "/jypy/zxxx/", "amss"),
    RealisticSiteProfile("ict", "/yjsjy/zsxx/sszs/", "ict"),
    RealisticSiteProfile("is_cas", "/yjsjy/zsxx/", "is_with_documents"),
    RealisticSiteProfile("ipe", "/zsxx/", "legacy_table_gbk", "gbk"),
    RealisticSiteProfile("sia", "/zpjy/yjsjy/", "sia"),
    RealisticSiteProfile("iphy", "/?q=list2&id=3277", "query_article"),
    RealisticSiteProfile("ime", "/kjrh/tzggkjrh/", "ime"),
    RealisticSiteProfile("semi", "/yanjiusheng/channels/691.html", "semi"),
)


class RealisticAdmissionSite:
    """A localhost replica of one real site's server-rendered list structure."""

    def __init__(self, profile: RealisticSiteProfile, initial_count: int = 3):
        self.profile = profile
        self._lock = threading.RLock()
        self._next_id = 1
        self.announcements: list[SimulatedAnnouncement] = []
        self.decorative_footer = "Original realistic-site footer"
        for index in range(initial_count):
            self._append_locked(
                f"{profile.key} initial admission notice {index + 1}",
                f"{profile.key.upper()}-INITIAL-DETAIL-{index + 1}",
            )

        handler_class = _make_realistic_handler_class(self)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.list_url = self.base_url + profile.list_path
        self._thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"pgam-realistic-site-{profile.key}",
            daemon=True,
        )
        self._started = False

    @property
    def name(self) -> str:
        return self.profile.key

    @property
    def origin_url(self) -> str:
        return REALISTIC_SITE_ORIGINS[self.profile.key]

    def __enter__(self) -> RealisticAdmissionSite:
        self.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def close(self) -> None:
        if self._started:
            self.server.shutdown()
        self.server.server_close()
        if self._started:
            self._thread.join(timeout=5)
        self._started = False

    def publish(self, title: str, body: str | None = None) -> SimulatedAnnouncement:
        with self._lock:
            return self._append_locked(title, body or f"{self.profile.key.upper()}-NEW-DETAIL-MARKER")

    def change_decoration(self, text: str) -> None:
        with self._lock:
            self.decorative_footer = text

    def _append_locked(self, title: str, body: str) -> SimulatedAnnouncement:
        announcement = SimulatedAnnouncement(
            id=self._next_id,
            token=uuid.uuid4().hex,
            title=title,
            body=body,
            published_at=datetime.combine(date(2026, 9, min(self._next_id, 28)), time(12)),
        )
        self._next_id += 1
        self.announcements.append(announcement)
        return announcement

    def _announcement_by_path(self, path: str) -> SimulatedAnnouncement | None:
        with self._lock:
            for item in self.announcements:
                if path == self._detail_path(item):
                    return item
        return None

    def _announcement_by_query(self, query: str) -> SimulatedAnnouncement | None:
        values = parse_qs(query)
        if values.get("q", [""])[0] != "moredetail":
            return None
        try:
            item_id = int(values.get("id", ["0"])[0])
        except ValueError:
            return None
        with self._lock:
            return next((item for item in self.announcements if item.id == item_id), None)

    def _list_directory(self) -> str:
        path = self.profile.list_path.split("?", 1)[0]
        if path.endswith("/"):
            return path
        return path.rsplit("/", 1)[0] + "/"

    def _relative_detail_href(self, item: SimulatedAnnouncement) -> str:
        if self.profile.layout == "pseudo_link":
            return f"detail/{item.token}"
        if self.profile.layout == "onclick_rows":
            return f"/article/{item.id}/182?num=-1"
        if self.profile.layout == "query_article":
            return f"?q=moredetail&id={item.id}"
        return f"news/{item.id}.html"

    def _detail_path(self, item: SimulatedAnnouncement) -> str:
        if self.profile.layout == "pseudo_link":
            return f"{self._list_directory()}detail/{item.token}"
        if self.profile.layout == "onclick_rows":
            return f"/article/{item.id}/182"
        if self.profile.layout == "query_article":
            return "/"
        return f"{self._list_directory()}news/{item.id}.html"

    def _visible_items(self) -> list[SimulatedAnnouncement]:
        with self._lock:
            return list(reversed(self.announcements))

    def _render_list(self) -> tuple[bytes, str]:
        items = self._visible_items()
        layout = self.profile.layout
        chunks: list[str] = []

        if layout in {"ia_cas", "amss", "ict", "is_with_documents", "sia"}:
            for item in items:
                date_node = f'<span class="data-s">{item.published_at:%Y-%m-%d}</span>'
                if layout == "amss":
                    date_node = f'<span class="box-date">{item.published_at:%Y-%m-%d}</span>'
                elif layout == "ict":
                    date_node = f'<span class="pull-right">[{item.published_at:%Y-%m-%d}]</span>'
                elif layout == "is_cas":
                    date_node = f'<span class="date">{item.published_at:%Y-%m-%d}</span>'
                elif layout == "sia":
                    date_node = f'<span class="right">{item.published_at:%Y-%m-%d}</span>'
                chunks.append(
                    f'<li><a href="{html.escape(self._relative_detail_href(item))}" '
                    f'target="_blank">{html.escape(item.title)}</a>{date_node}</li>'
                )
            container_class = {
                "ia_cas": ' class="wenzi-item" id="content"',
                "amss": ' class="list-txt-02" id="content"',
                "ict": ' class="clearfix"',
                "is_with_documents": ' id="content"',
                "sia": ' id="content"',
            }[layout]
            body = f"<ul{container_class}>{''.join(chunks)}</ul>"
            if layout == "is_with_documents":
                documents = "".join(
                    f'<li><a href="files/document-{index}.doc">Admission brochure document {index}</a></li>'
                    for index in range(1, 4)
                )
                body = f"<ul id='document-list'>{documents}</ul>{body}"

        elif layout in {"tsinghua_life", "ime", "semi"}:
            for item in items:
                published = item.published_at.strftime("%Y-%m-%d")
                if layout == "tsinghua_life":
                    chunks.append(
                        f'<li><a href="{self._relative_detail_href(item)}" target="_blank" '
                        f'title="{html.escape(item.title)}"><span>{published}</span> '
                        f"{html.escape(item.title)}</a></li>"
                    )
                elif layout == "ime":
                    chunks.append(
                        f'<li><a href="{self._relative_detail_href(item)}">{html.escape(item.title)}</a>'
                        f"<span>{item.published_at:%Y/%m/%d}</span></li>"
                    )
                else:
                    chunks.append(
                        f'<li><a href="{self._relative_detail_href(item)}" target="_blank">'
                        f'{html.escape(item.title)}<span class="time right">'
                        f"({item.published_at:%Y-%m-%d})</span></a></li>"
                    )
            container = {
                "tsinghua_life": '<div class="train contbtmpd clearfix"><ul class="itemlist">',
                "ime": '<ul class="comment_list clearfix">',
                "semi": '<ul class="category">',
            }[layout]
            body = container + "".join(chunks) + ("</ul></div>" if layout == "tsinghua_life" else "</ul>")

        elif layout == "pseudo_link":
            for item in items:
                chunks.append(
                    "<li><a data-val=\"{token}\" data-zslx=\"\" href=\"javascript:void(0);\">"
                    '<div class="time">{published}</div><div class="name">{title}</div></a></li>'.format(
                        token=item.token,
                        published=item.published_at.strftime("%Y-%m-%d"),
                        title=html.escape(item.title),
                    )
                )
            body = f'<ul id="content">{"".join(chunks)}</ul>'

        elif layout == "card_calendar":
            for item in items:
                chunks.append(
                    "<li><a class=\"a\" href=\"{href}\"><div class=\"time\"><div class=\"time_li\">"
                    "<h3>{day}</h3><h6>{year_month}</h6></div></div><div class=\"con\">"
                    "<h4 class=\"l2 h4s2\">{title}</h4></div></a></li>".format(
                        href=self._relative_detail_href(item),
                        day=item.published_at.day,
                        year_month=item.published_at.strftime("%Y-%m"),
                        title=html.escape(item.title),
                    )
                )
            body = f'<ul class="list16">{"".join(chunks)}</ul>'

        elif layout == "calendar_item":
            for item in items:
                chunks.append(
                    "<a class=\"item\" href=\"{href}\"><div class=\"calendar\"><div class=\"day\">{day}</div>"
                    "<div class=\"month\">{year_month}</div></div><div class=\"text-box\">"
                    '<div class="title">{title}</div></div></a>'.format(
                        href=self._relative_detail_href(item),
                        day=item.published_at.day,
                        year_month=item.published_at.strftime("%Y.%m"),
                        title=html.escape(item.title),
                    )
                )
            body = f'<div class="announcement-list">{"".join(chunks)}</div>'

        elif layout == "title_meta_spans":
            for item in items:
                chunks.append(
                    '<li class="news n{index} clearfix"><span class="news_title">'
                    '<a href="{href}" target="_blank" title="{title}">{title}</a></span>'
                    '<span class="news_meta">{published}</span></li>'.format(
                        index=item.id,
                        href=self._relative_detail_href(item),
                        title=html.escape(item.title),
                        published=item.published_at.strftime("%Y-%m-%d"),
                    )
                )
            body = f'<ul class="news_list list2">{"".join(chunks)}</ul>'

        elif layout == "fudan_with_directory_distractor":
            for item in items:
                chunks.append(
                    '<li class="cols n{index}"><span class="cols_title">'
                    '<a href="{href}" target="_blank" title="{title}">{title}</a></span>'
                    '<span class="cols_meta">{published}</span></li>'.format(
                        index=item.id,
                        href=self._relative_detail_href(item),
                        title=html.escape(item.title),
                        published=item.published_at.strftime("%Y-%m-%d"),
                    )
                )
            departments = "".join(
                f'<li class="link-item"><a href="/departments/{index}">'
                f"Department {index:03d} for admission</a></li>"
                for index in range(1, 18)
            )
            body = (
                f'<div id="wp_news_w13"><ul>{departments}</ul></div>'
                f'<div id="wp_news_w26"><ul class="cols_list clearfix">{"".join(chunks)}</ul></div>'
            )

        elif layout == "onclick_rows":
            for item in items:
                chunks.append(
                    "<div class=\"line-box-new\" target=\"_blank\" onclick=\"window.open('{href}','_blank')\">"
                    '<span class="txt" title="{title}">{title}</span>'
                    '<span class="data">{published}</span></div>'.format(
                        href=self._relative_detail_href(item),
                        title=html.escape(item.title),
                        published=item.published_at.strftime("%Y-%m-%d"),
                    )
                )
            body = f'<div id="xs1" class="right-list">{"".join(chunks)}</div>'

        elif layout == "legacy_table_gbk":
            for item in items:
                chunks.append(
                    "<tr><td class=\"hh14\"><a class=\"hh14\" href=\"{href}\" "
                    'target="_blank" title="{title}">{title}</a></td>'
                    "<td><span>{published}</span></td></tr>".format(
                        href=self._relative_detail_href(item),
                        title=html.escape(item.title),
                        published=item.published_at.strftime("%Y-%m-%d"),
                    )
                )
            distractor = "".join(
                f"<tr><td><a href='../other/category-{index}.html'>Other category notice {index}</a></td>"
                f"<td><span>2026-08-{index:02d}</span></td></tr>"
                for index in range(1, 5)
            )
            body = (
                f"<table class='black12h'><tbody>{distractor}</tbody></table>"
                f"<table class='news-list-table'><tbody>{''.join(chunks)}</tbody></table>"
            )

        elif layout == "query_article":
            for item in items:
                chunks.append(
                    '<a class="article-item" href="{href}"><div class="article-title">{title}</div>'
                    '<div class="article-time">{published}</div></a>'.format(
                        href=self._relative_detail_href(item),
                        title=html.escape(item.title),
                        published=item.published_at.strftime("%Y-%m-%d"),
                    )
                )
            body = f'<div class="info-wrap"><div class="article"><ul class="article-list">{"".join(chunks)}</ul></div></div>'

        else:
            raise ValueError(f"Unsupported realistic layout: {layout}")

        document = (
            "<!doctype html><html><head>"
            f'<meta charset="{self.profile.encoding}"><title>{html.escape(self.profile.key)} admission list</title>'
            "</head><body><nav><a href='/'>Navigation</a></nav>"
            f"{body}<footer>{html.escape(self.decorative_footer)}</footer></body></html>"
        )
        encoding = "gbk" if self.profile.encoding.lower() in {"gbk", "gb2312", "gb18030"} else "utf-8"
        return document.encode(encoding), f"text/html; charset={encoding}"

    def _render_detail(self, announcement: SimulatedAnnouncement) -> tuple[bytes, str]:
        document = f"""<!doctype html>
<html><head><meta charset="{self.profile.encoding}">
<title>{html.escape(announcement.title)}</title></head><body><article>
<h1>{html.escape(announcement.title)}</h1>
<p>{html.escape(announcement.body)}</p>
<p>Published at {announcement.published_at.isoformat()}</p>
</article><footer>{html.escape(self.decorative_footer)}</footer></body></html>"""
        encoding = "gbk" if self.profile.encoding.lower() in {"gbk", "gb2312", "gb18030"} else "utf-8"
        return document.encode(encoding), f"text/html; charset={encoding}"


def _make_realistic_handler_class(site: RealisticAdmissionSite) -> type[BaseHTTPRequestHandler]:
    class RealisticSiteHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = unquote(parsed.path)
            profile_url = urlparse(site.profile.list_path)
            list_query = parse_qs(profile_url.query, keep_blank_values=True)
            request_query = parse_qs(parsed.query, keep_blank_values=True)
            announcement: SimulatedAnnouncement | None = None
            is_list = path == profile_url.path and (
                not list_query or all(request_query.get(key) == value for key, value in list_query.items())
            )
            if is_list:
                content, content_type = site._render_list()
                self._send(200, content, content_type)
                return
            if path == "/":
                announcement = site._announcement_by_query(parsed.query)
            else:
                announcement = site._announcement_by_path(path)
            if announcement is None:
                self._send(404, b"Notice not found", "text/plain; charset=utf-8")
                return
            content, content_type = site._render_detail(announcement)
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

    return RealisticSiteHandler


class RealisticAdmissionPlatform:
    """Sixteen independent localhost sites mirroring the supplied real list layouts."""

    def __init__(self) -> None:
        initial_counts = {"iphy": 1}
        self.sites = [
            RealisticAdmissionSite(profile, initial_counts.get(profile.key, 3))
            for profile in REALISTIC_SITE_PROFILES
        ]

    def __enter__(self) -> RealisticAdmissionPlatform:
        for site in self.sites:
            site.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        for site in self.sites:
            site.close()


if __name__ == "__main__":
    with RealisticAdmissionPlatform() as platform:
        for site in platform.sites:
            print(site.name, site.list_url)
