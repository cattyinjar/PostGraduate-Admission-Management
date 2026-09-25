from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:  # pragma: no cover - dependency is installed in normal runtime
    trafilatura = None

from .adapters import clean_text, parse_datetime

MAX_LLM_TEXT_LENGTH = 24_000
ATTACHMENT_EXTENSIONS = {
    ".pdf": "PDF",
    ".doc": "Word",
    ".docx": "Word",
    ".xls": "Excel",
    ".xlsx": "Excel",
    ".zip": "压缩包",
    ".rar": "压缩包",
}


@dataclass(slots=True)
class Attachment:
    name: str
    url: str
    type: str


@dataclass(slots=True)
class DetailContent:
    title: str
    text: str
    published_at: datetime | None
    url: str | None
    attachments: list[Attachment] = field(default_factory=list)
    degraded: bool = False


class DetailExtractor:
    def extract(self, base_url: str, content: bytes, encoding: str = "", content_type: str = "") -> DetailContent:
        from .adapters import decode_content

        html = decode_content(content, encoding, content_type)
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = self._title(soup)
        published = self._published(soup)
        attachments = self._attachments(soup, base_url)
        text = ""
        if trafilatura is not None:
            try:
                extracted = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    favor_recall=True,
                )
                text = clean_text(extracted)
            except Exception:
                # A packaged third-party extractor may lack its settings file or data.
                # Keep monitoring available by falling back to the local HTML extractor.
                text = ""
        if not text:
            node = soup.find("article") or soup.find("main") or soup.find(class_=re.compile("content|article", re.I)) or soup.body
            text = clean_text(node.get_text("\n", strip=True) if node else "")
        if len(text) > MAX_LLM_TEXT_LENGTH:
            text = text[:MAX_LLM_TEXT_LENGTH]
        degraded = not text or text == title
        if degraded:
            text = title or "正文内容暂不可用，请访问原文链接查看。"
        return DetailContent(title=title, text=text, published_at=published, url=base_url, attachments=attachments, degraded=degraded)

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        og = soup.find("meta", property="og:title")
        if isinstance(og, dict):  # BeautifulSoup Tag behaves as dict-like; retained for typing safety
            candidate = og.get("content")
        elif og is not None:
            candidate = og.get("content")
        else:
            candidate = soup.title.get_text(strip=True) if soup.title else ""
        return clean_text(str(candidate or "")) or "未命名通知"

    @staticmethod
    def _published(soup: BeautifulSoup) -> datetime | None:
        for selector in ["meta[property='article:published_time']", "meta[name='publishdate']", "meta[name='pubdate']"]:
            node = soup.select_one(selector)
            if node:
                parsed = parse_datetime(node.get("content"))
                if parsed:
                    return parsed
        body = soup.body.get_text(" ", strip=True) if soup.body else ""
        return parse_datetime(body)

    @staticmethod
    def _attachments(soup: BeautifulSoup, base_url: str) -> list[Attachment]:
        result: list[Attachment] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href", ""))
            lower = href.lower()
            extension = next((ext for ext in ATTACHMENT_EXTENSIONS if lower.split("?", 1)[0].endswith(ext)), None)
            if extension is None:
                continue
            url = urljoin(base_url, href)
            if url in seen:
                continue
            seen.add(url)
            name = clean_text(anchor.get_text(" ", strip=True)) or url.rsplit("/", 1)[-1]
            result.append(Attachment(name=name, url=url, type=ATTACHMENT_EXTENSIONS[extension]))
        return result
