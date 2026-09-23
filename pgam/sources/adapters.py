from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from ..core.models import AdapterConfig, MonitorTask, NormalizedListItem, SourceType

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "spm", "from", "scene",
}
DATE_PATTERNS = [
    re.compile(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"),
]
BAD_PARENT = re.compile(r"nav|menu|footer|friend|link-item|link-items|links-wrap|botlinks|mod-link|search|breadcrumb|pagination|copyright", re.I)
BAD_LINK_SUFFIX = {
    ".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".ico", ".svg", ".webp", ".zip", ".rar", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
}


class AdapterError(ValueError):
    pass


def clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() not in TRACKING_PARAMS]
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), ""))


def parse_datetime(value: str | None) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            month = int(match.group(2))
            day = int(match.group(3))
            try:
                return datetime(int(match.group(1)), month, day)
            except ValueError:
                continue
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            return parsed
        return parsed
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def fingerprint_url(url: str | None) -> str | None:
    canonical = canonical_url(url)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest() if canonical else None


def make_item(task: MonitorTask, title: str, url: str | None, published_at: datetime | None) -> NormalizedListItem:
    clean_title = clean_text(title)
    if not clean_title:
        raise AdapterError("List item has no readable title")
    url_hash = fingerprint_url(url)
    if url_hash:
        return NormalizedListItem(clean_title, canonical_url(url), published_at, url_hash)
    date_text = published_at.isoformat() if published_at else ""
    identity = f"{task.id}|{clean_title.casefold()}|{date_text}"
    return NormalizedListItem(clean_title, None, published_at, hashlib.sha256(identity.encode()).hexdigest())


@dataclass(slots=True)
class ExtractionOutcome:
    items: list[NormalizedListItem]
    source_type: SourceType


class RssAdapter:
    def extract(self, task: MonitorTask, content: bytes, content_type: str, encoding: str) -> list[NormalizedListItem]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise AdapterError(f"RSS XML parse failed: {exc}") from exc
        namespace = root.tag.split("}", 1)[0] + "}" if "}" in root.tag else ""
        entries: list[Tag] = []
        if root.tag.lower().endswith("rss"):
            entries = list(root.findall("channel/item"))
        elif root.tag.lower().endswith("feed") or root.tag.endswith("}feed"):
            entries = list(root.findall(f"{namespace}entry"))
        else:
            entries = list(root.findall("channel/item")) + list(root.findall(f"{namespace}entry"))
        items: list[NormalizedListItem] = []
        seen: set[str] = set()
        for entry in entries:
            title = self._child_text(entry, namespace, "title")
            link = self._link(entry, namespace)
            published = parse_datetime(
                self._child_text(entry, namespace, "pubDate")
                or self._child_text(entry, namespace, "published")
                or self._child_text(entry, namespace, "updated")
            )
            guid = self._child_text(entry, namespace, "id") or self._child_text(entry, namespace, "guid")
            canonical = canonical_url(link or guid)
            fingerprint = fingerprint_url(link or guid)
            if not title or not fingerprint:
                continue
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            items.append(NormalizedListItem(clean_text(title), canonical, published, fingerprint))
        return items

    @staticmethod
    def _child_text(entry: Any, namespace: str, name: str) -> str | None:
        direct = entry.findtext(name) if not namespace else None
        namespaced = entry.findtext(f"{namespace}{name}") if namespace else None
        return direct or namespaced

    @staticmethod
    def _link(entry: Any, namespace: str) -> str | None:
        node = entry.find("link") if not namespace else entry.find(f"{namespace}link")
        if node is None:
            return None
        if node.attrib.get("href"):
            return node.attrib["href"]
        return node.text


class StaticListAdapter:
    """Extract announcement links from server-rendered HTML.

    The adapter deliberately keeps auto-detection conservative, but it understands three
    common Chinese university implementations beyond plain anchors: pseudo links backed by
    ``data-val``, clickable ``div`` rows backed by ``onclick="window.open(...)"``, and
    small semantic lists containing only one current announcement.
    """

    TITLE_CLASS_PATTERN = re.compile(r"(?:^|[-_ ])(?:name|title|txt|topic|subject)(?:$|[-_ ])", re.I)
    DATE_CLASS_PATTERN = re.compile(r"(?:^|[-_ ])(?:time|date|data|meta|pubtime|published)(?:$|[-_ ])", re.I)
    LIST_SEMANTIC_PATTERN = re.compile(r"news|notice|article|content|list|category|comment", re.I)
    WINDOW_OPEN_PATTERN = re.compile(r"window\.open\(\s*(['\"])(.*?)\1", re.I)
    LEADING_DAY_PATTERN = re.compile(r"^\s*\d{1,2}\s+(?=20\d{2})")
    CATEGORY_TITLE_PATTERN = re.compile(r"^\[[^\]]+\]$")
    EMPTY_PARENTHESES_PATTERN = re.compile(r"\s*\(\s*\)\s*$")

    def extract(self, task: MonitorTask, content: bytes, content_type: str, encoding: str) -> list[NormalizedListItem]:
        decoded = decode_content(content, encoding or task.adapter.encoding, content_type)
        soup = BeautifulSoup(decoded, "lxml")
        config = task.adapter
        items = self._selected_items(task, soup, config) if config.item_selector else self._auto_items(task, soup)
        unique: dict[str, NormalizedListItem] = {}
        for item in items:
            unique[item.fingerprint] = item
        return list(unique.values())

    def _selected_items(
        self, task: MonitorTask, soup: BeautifulSoup, config: AdapterConfig
    ) -> list[NormalizedListItem]:
        result: list[NormalizedListItem] = []
        for node in soup.select(config.item_selector):
            if not isinstance(node, Tag):
                continue
            link_node: Tag | None = node.select_one(config.link_selector) if config.link_selector else None
            if link_node is None and node.name == "a":
                link_node = node
            if link_node is None:
                link_node = node.find("a", href=True)
            title_node = node.select_one(config.title_selector) if config.title_selector else None
            date_node = node.select_one(config.date_selector) if config.date_selector else None
            href = self._node_url(task, link_node or node)
            title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
            if not title:
                title = self._node_title(link_node or node)
            if not title:
                title = self._node_title(node)
            published = self._node_date(date_node or node)
            if title:
                result.append(make_item(task, title, href, published))
        return result

    def _auto_items(self, task: MonitorTask, soup: BeautifulSoup) -> list[NormalizedListItem]:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        candidates = self._candidate_nodes(task, soup)
        if not candidates:
            raise AdapterError("No usable announcement links were found on this page")

        parents: dict[int, Tag] = {}
        votes: dict[int, list[tuple[Tag, datetime | None]]] = {}
        for node, published in candidates:
            current: Tag | None = node.parent
            for _ in range(6):
                if current is None or current.name in {"body", "html", "[document]"}:
                    break
                key = id(current)
                parents[key] = current
                votes.setdefault(key, []).append((node, published))
                current = current.parent

        ranked: list[tuple[float, int]] = []
        for key, parent in parents.items():
            rows = votes[key]
            score = self._container_score(parent, rows, task)
            if score is not None:
                ranked.append((score, key))
        if not ranked:
            raise AdapterError("Repeated announcement list structure was not found")
        ranked.sort(reverse=True)
        parent = parents[ranked[0][1]]
        selected = rows = votes[ranked[0][1]]

        strong_semantic = bool(self.LIST_SEMANTIC_PATTERN.search(self._signature(parent)))
        dated = sum(published is not None for _, published in rows)
        if len(rows) < 3 and not (len(rows) >= 1 and strong_semantic and dated == len(rows)):
            raise AdapterError("Repeated announcement list structure was not found")

        items: list[NormalizedListItem] = []
        seen: set[str] = set()
        for node, published in selected:
            href = self._node_url(task, node)
            title = self._node_title(node)
            if not title:
                continue
            item = make_item(task, title, href, published)
            if item.fingerprint in seen:
                continue
            seen.add(item.fingerprint)
            items.append(item)
        if not items:
            raise AdapterError("No announcement titles could be extracted")
        return items

    def _candidate_nodes(self, task: MonitorTask, soup: BeautifulSoup) -> list[tuple[Tag, datetime | None]]:
        nodes: list[Tag] = list(soup.find_all("a", href=True))
        nodes.extend(node for node in soup.find_all(attrs={"onclick": True}) if node not in nodes)
        nodes.extend(
            node
            for node in soup.find_all(attrs={"data-href": True})
            if node.name != "a" and node not in nodes
        )
        result: list[tuple[Tag, datetime | None]] = []
        seen: set[int] = set()
        task_host = urlparse(task.url).netloc.lower()
        for node in nodes:
            if not isinstance(node, Tag) or id(node) in seen:
                continue
            title = self._node_title(node)
            url = self._node_url(task, node)
            published = self._node_date(node)
            if not title or len(title) < 6 or not url:
                continue
            if self.CATEGORY_TITLE_PATTERN.match(title):
                continue
            if self._has_bad_ancestor(node) or self._unacceptable_url(url):
                continue
            parsed = urlparse(url)
            if parsed.netloc.lower() != task_host and published is None:
                continue
            seen.add(id(node))
            result.append((node, published))
        return result

    def _container_score(
        self, parent: Tag, rows: list[tuple[Tag, datetime | None]], task: MonitorTask
    ) -> float | None:
        task_host = urlparse(task.url).netloc.lower()
        task_directory = self._url_directory(task.url)
        signature = self._signature(parent)
        if BAD_PARENT.search(signature):
            return None
        count = len(rows)
        dated = sum(published is not None for _, published in rows)
        urls = [self._node_url(task, row[0]) for row in rows]
        unique_urls = len({url for url in urls if url})
        same_host = sum(bool(url) and urlparse(url).netloc.lower() == task_host for url in urls)
        semantic = bool(self.LIST_SEMANTIC_PATTERN.search(signature))
        element_children = [child for child in parent.children if isinstance(child, Tag)]
        item_wrappers = sum(self._contains_one(row[0], child) for child in element_children for row in rows)
        wrapper_ratio = item_wrappers / max(len(element_children), 1)
        title_lengths = [len(self._node_title(row[0])) for row in rows]
        average_title = sum(title_lengths) / max(len(title_lengths), 1)
        text_size = len(parent.get_text(" ", strip=True))
        # A useful list node is compact. This penalty prevents body-like wrappers from
        # defeating a deeply nested news list.
        compactness = max(-12.0, min(0.0, 8.0 - text_size / max(count, 1) / 80.0))
        dated_ratio = dated / max(count, 1)
        path_affinity = (
            sum(bool(url) and urlparse(url).path.startswith(task_directory) for url in urls)
            / max(count, 1)
        )
        score = (
            min(count, 35) * 1.2
            + dated * 8.0
            + dated_ratio * 8.0
            - (1.0 - dated_ratio) * 6.0
            + path_affinity * 20.0
            + unique_urls * 0.7
            + same_host * 0.4
            + (5.0 if semantic else 0.0)
            + min(wrapper_ratio, 1.0) * 5.0
            + min(average_title / 18.0, 2.0)
            + compactness
        )
        # One-item lists are accepted only when their class/id and dates strongly identify
        # an announcement container.
        if count < 3 and not (semantic and dated == count):
            return None
        return score

    def _node_title(self, node: Tag | None) -> str:
        if node is None:
            return ""
        explicit = clean_text(str(node.get("title") or ""))
        if explicit:
            return explicit
        for heading in node.find_all(["h1", "h2", "h3", "h4"]):
            value = clean_text(heading.get_text(" ", strip=True))
            if len(value) >= 6 and not DATE_PATTERNS[0].search(value):
                return value
        for child in node.find_all(True):
            classes = self._classes(child)
            if child.name in {"span", "div", "p", "h1", "h2", "h3", "h4"} and any(
                self.TITLE_CLASS_PATTERN.search(f" {value} ") for value in classes
            ):
                value = clean_text(child.get_text(" ", strip=True))
                if value:
                    return value
        text = clean_text(node.get_text(" ", strip=True))
        text = self.LEADING_DAY_PATTERN.sub("", text, count=1)
        text = DATE_PATTERNS[0].sub("", text)
        text = self.EMPTY_PARENTHESES_PATTERN.sub("", text)
        return clean_text(text)

    def _node_date(self, node: Tag | None) -> datetime | None:
        if node is None:
            return None
        for attribute in ("datetime", "data-time", "data-date"):
            parsed = parse_datetime(str(node.get(attribute) or ""))
            if parsed:
                return parsed
        for child in node.find_all(True):
            classes = self._classes(child)
            if any(self.DATE_CLASS_PATTERN.search(f" {value} ") for value in classes):
                parsed = parse_datetime(str(child.get_text(" ", strip=True)))
                if parsed:
                    return parsed
        month_node = node.find(class_="month") or node.find("h6")
        day_node = node.find(class_="day") or node.find("h3")
        if month_node is not None and day_node is not None:
            parsed = parse_datetime(
                f"{clean_text(month_node.get_text())}.{clean_text(day_node.get_text())}"
            )
            if parsed:
                return parsed
        current: Tag | None = node
        for _ in range(3):
            if current is None:
                break
            parsed = parse_datetime(current.get_text(" ", strip=True))
            if parsed:
                return parsed
            current = current.parent
        return None

    def _node_url(self, task: MonitorTask, node: Tag | None) -> str | None:
        if node is None:
            return None
        for attribute in ("data-href", "data-url"):
            value = clean_text(str(node.get(attribute) or ""))
            if value:
                return urljoin(task.url, value)
        href = clean_text(str(node.get("href") or ""))
        if href and not href.startswith(("#", "mailto:", "tel:")):
            if not href.lower().startswith("javascript:"):
                return urljoin(task.url, href)
        onclick = clean_text(str(node.get("onclick") or ""))
        match = self.WINDOW_OPEN_PATTERN.search(onclick)
        if match:
            return urljoin(task.url, match.group(2))
        value = clean_text(str(node.get("data-val") or ""))
        if value and href.lower().startswith("javascript:"):
            parsed = urlparse(task.url)
            if parsed.path.rstrip("/").endswith("/list"):
                detail_path = parsed.path.rstrip("/")[: -len("list")] + f"detail/{value}"
                return urlunparse(parsed._replace(path=detail_path))
        return None

    def _safe_url(self, node: Tag) -> str | None:
        for attribute in ("data-href", "data-url"):
            value = clean_text(str(node.get(attribute) or ""))
            if value:
                return urljoin("https://example.invalid/", value)
        href = clean_text(str(node.get("href") or ""))
        if href and not href.lower().startswith(("javascript:", "#", "mailto:", "tel:")):
            return urljoin("https://example.invalid/", href)
        onclick = clean_text(str(node.get("onclick") or ""))
        match = self.WINDOW_OPEN_PATTERN.search(onclick)
        if match:
            return urljoin("https://example.invalid/", match.group(2))
        return None

    @staticmethod
    def _url_directory(url: str) -> str:
        path = urlparse(url).path or "/"
        if path.endswith("/"):
            return path
        parent = path.rsplit("/", 1)[0]
        return f"{parent}/" if parent else "/"

    def _unacceptable_url(self, url: str) -> bool:
        lower = urlparse(url).path.lower()
        return lower.endswith(tuple(BAD_LINK_SUFFIX))

    def _has_bad_ancestor(self, node: Tag) -> bool:
        current: Tag | None = node
        for _ in range(5):
            if current is None or current.name in {"body", "html", "[document]"}:
                return False
            signature = self._signature(current)
            if BAD_PARENT.search(signature):
                return True
            if self.LIST_SEMANTIC_PATTERN.search(signature):
                return False
            current = current.parent
        return False


    @staticmethod
    def _contains_one(candidate: Tag, wrapper: Tag) -> bool:
        return any(candidate is descendant for descendant in wrapper.descendants)

    @staticmethod
    def _classes(node: Tag) -> list[str]:
        value = node.get("class", [])
        if isinstance(value, str):
            return [value]
        return [str(item) for item in value]

    @staticmethod
    def _signature(node: Tag) -> str:
        return " ".join([node.name, *StaticListAdapter._classes(node), str(node.get("id") or "")])


def decode_content(content: bytes, encoding_override: str | None, content_type: str = "") -> str:
    candidates: list[str] = []
    if encoding_override:
        candidates.append(encoding_override)
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig", errors="replace")
    content_type_lower = content_type.lower()
    if "charset=" in content_type_lower:
        candidates.append(content_type_lower.split("charset=", 1)[1].split(";", 1)[0].strip())
    head = content[:4096].decode("ascii", errors="ignore").lower()
    match = re.search(r'charset=["\']?([\w-]+)', head)
    if match:
        candidates.append(match.group(1))
    candidates.extend(["utf-8", "gb18030"])
    for candidate in candidates:
        try:
            return content.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


class SourceAdapterRegistry:
    def __init__(self) -> None:
        self.rss = RssAdapter()
        self.static = StaticListAdapter()

    def sniff(self, content: bytes, content_type: str, decoded_head: str) -> SourceType:
        lowered = decoded_head[:1000].lstrip().lower()
        if "rss" in content_type.lower() or "<rss" in lowered or "<feed" in lowered:
            return SourceType.RSS
        return SourceType.STATIC_LIST

    def extract(self, task: MonitorTask, content: bytes, content_type: str, encoding: str) -> ExtractionOutcome:
        if task.source_type == SourceType.RSS:
            return ExtractionOutcome(self.rss.extract(task, content, content_type, encoding), SourceType.RSS)
        if task.source_type == SourceType.STATIC_LIST:
            return ExtractionOutcome(self.static.extract(task, content, content_type, encoding), SourceType.STATIC_LIST)
        decoded = decode_content(content, task.adapter.encoding, content_type)
        source_type = self.sniff(content, content_type, decoded)
        if source_type == SourceType.RSS:
            return ExtractionOutcome(self.rss.extract(task, content, content_type, encoding), SourceType.RSS)
        return ExtractionOutcome(self.static.extract(task, content, content_type, encoding), SourceType.STATIC_LIST)
