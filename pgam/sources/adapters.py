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
BAD_PARENT = re.compile(r"nav|menu|footer|friend|search|breadcrumb|pagination|copyright", re.I)
BAD_LINK_SUFFIX = {
    ".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".ico", ".svg", ".webp", ".zip", ".rar",
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
    def extract(self, task: MonitorTask, content: bytes, content_type: str, encoding: str) -> list[NormalizedListItem]:
        decoded = decode_content(content, encoding or task.adapter.encoding, content_type)
        soup = BeautifulSoup(decoded, "lxml")
        config = task.adapter
        if config.item_selector:
            items = self._selected_items(task, soup, config)
        else:
            items = self._auto_items(task, soup)
        unique: dict[str, NormalizedListItem] = {}
        for item in items:
            unique[item.fingerprint] = item
        return list(unique.values())

    def _selected_items(
        self, task: MonitorTask, soup: BeautifulSoup, config: AdapterConfig
    ) -> list[NormalizedListItem]:
        result: list[NormalizedListItem] = []
        for node in soup.select(config.item_selector):
            link_node: Tag | None = node.select_one(config.link_selector) if config.link_selector else node.find("a")
            title_node = node.select_one(config.title_selector) if config.title_selector else link_node
            date_node = node.select_one(config.date_selector) if config.date_selector else node
            href = link_node.get("href") if isinstance(link_node, Tag) else None
            title = clean_text(title_node.get_text(" ", strip=True) if title_node else "") or clean_text(
                link_node.get_text(" ", strip=True) if link_node else node.get_text(" ", strip=True)
            )
            published = parse_datetime(date_node.get_text(" ", strip=True) if date_node else None)
            if title:
                result.append(make_item(task, title, urljoin(task.url, href) if href else None, published))
        return result

    def _auto_items(self, task: MonitorTask, soup: BeautifulSoup) -> list[NormalizedListItem]:
        for tag in soup(["script", "style"]):
            tag.decompose()
        anchors = [
            anchor for anchor in soup.find_all("a", href=True)
            if self._acceptable_anchor(task, anchor)
        ]
        if not anchors:
            raise AdapterError("No usable announcement links were found on this page")
        containers = [self._announcement_container(anchor) for anchor in anchors]
        valid_containers = [node for node in containers if node is not None]
        if not valid_containers:
            raise AdapterError("Repeated announcement list structure was not found")
        items: list[NormalizedListItem] = []
        seen: set[str] = set()
        for container in valid_containers:
            for anchor in container.find_all("a", href=True):
                if not self._acceptable_anchor(task, anchor):
                    continue
                href = urljoin(task.url, str(anchor.get("href", "")))
                title = clean_text(anchor.get_text(" ", strip=True))
                published = parse_datetime(container.get_text(" ", strip=True))
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

    @staticmethod
    def _acceptable_anchor(task: MonitorTask, anchor: Tag) -> bool:
        href = str(anchor.get("href", "")).strip().lower()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            return False
        if any(href.endswith(suffix) for suffix in BAD_LINK_SUFFIX):
            return False
        text = clean_text(anchor.get_text(" ", strip=True))
        if len(text) < 6:
            return False
        parent_classes = " ".join(
            str(value) for value in [anchor.get("class", []), anchor.get("id", "")]
        )
        if BAD_PARENT.search(parent_classes):
            return False
        absolute = urljoin(task.url, str(anchor.get("href", "")))
        parsed = urlparse(absolute)
        task_host = urlparse(task.url).netloc.lower()
        return not parsed.netloc or parsed.netloc.lower() == task_host

    @staticmethod
    def _announcement_container(anchor: Tag) -> Tag | None:
        current: Tag | None = anchor.parent
        for _ in range(4):
            if current is None or current.name in {"body", "html", "[document]"}:
                return None
            signature = " ".join(
                str(value) for value in [current.get("class", []), current.get("id", "")]
            )
            if BAD_PARENT.search(signature):
                return None
            links = current.find_all("a", href=True)
            titles = [clean_text(link.get_text(" ", strip=True)) for link in links]
            if len(links) >= 3 and sum(len(title) >= 6 for title in titles) >= 3:
                return current
            current = current.parent
        return None


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
