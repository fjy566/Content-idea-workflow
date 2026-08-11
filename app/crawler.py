from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil.parser import parse as parse_date

from .config import settings
from .models import Source
from .security import validate_public_url
from .utils import clean_text, content_hash, normalize_url


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CrawledItem:
    title: str
    url: str
    summary: str
    content: str
    published_at: datetime | None
    metadata: dict[str, Any]
    content_hash: str


def _parse_datetime(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, tuple):
        try:
            result = datetime(*value[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    else:
        try:
            result = parsedate_to_datetime(str(value))
        except (TypeError, ValueError, OverflowError):
            try:
                result = parse_date(str(value), fuzzy=True)
            except (TypeError, ValueError, OverflowError):
                return None
    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _download(url: str) -> tuple[bytes, str]:
    validate_public_url(url)
    headers = {"User-Agent": settings.user_agent, "Accept": "text/html,application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.8"}
    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=settings.request_timeout_seconds,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > settings.max_response_bytes:
            raise ValueError(f"response is larger than {settings.max_response_bytes} bytes")
        body = response.content
        if len(body) > settings.max_response_bytes:
            raise ValueError(f"response is larger than {settings.max_response_bytes} bytes")
        return body, response.headers.get("content-type", "")


def _build_item(
    title: object | None,
    url: object | None,
    summary: object | None,
    content: object | None,
    published_at: datetime | None,
    source_url: str,
    metadata: dict[str, Any] | None = None,
) -> CrawledItem | None:
    title_text = clean_text(title, 1000)
    normalized_url = normalize_url(str(url or ""), source_url)
    if len(title_text) < 3 or not normalized_url.startswith(("http://", "https://")):
        return None
    summary_text = clean_text(summary, 20_000)
    content_text = clean_text(content, 50_000)
    return CrawledItem(
        title=title_text,
        url=normalized_url,
        summary=summary_text,
        content=content_text,
        published_at=published_at,
        metadata=metadata or {},
        content_hash=content_hash(title_text, normalized_url, summary_text),
    )


def _parse_rss(body: bytes, source_url: str) -> list[CrawledItem]:
    parsed = feedparser.parse(body)
    items: list[CrawledItem] = []
    for entry in parsed.entries:
        summary = entry.get("summary", "")
        content = "\n".join(
            str(part.get("value", "")) for part in entry.get("content", []) if isinstance(part, dict)
        )
        item = _build_item(
            entry.get("title"),
            entry.get("link"),
            summary,
            content,
            _parse_datetime(entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published")),
            source_url,
            {"author": clean_text(entry.get("author")), "tags": [tag.get("term") for tag in entry.get("tags", []) if isinstance(tag, dict)]},
        )
        if item:
            items.append(item)
    return items


def _select_text(node, selector: str | None) -> str:
    if selector:
        target = node.select_one(selector)
        return clean_text(target.get_text(" ", strip=True) if target else "")
    return clean_text(node.get_text(" ", strip=True))


def _select_attr(node, selector: str | None, attribute: str) -> str:
    target = node.select_one(selector) if selector else node.select_one("a[href]")
    if not target:
        return ""
    return clean_text(target.get(attribute, ""))


def _parse_html(body: bytes, source: Source) -> list[CrawledItem]:
    soup = BeautifulSoup(body, "html.parser")
    selector = source.item_selector or "article, .article, .item, .feed-item, li"
    nodes = soup.select(selector)
    if not nodes:
        nodes = soup.select("h1, h2, h3")
    items: list[CrawledItem] = []
    for node in nodes:
        title = _select_text(node, source.title_selector)
        if not title and node.name in {"h1", "h2", "h3"}:
            title = clean_text(node.get_text(" ", strip=True))
        link = _select_attr(node, source.link_selector, "href")
        summary = _select_text(node, source.summary_selector)
        published = _select_text(node, source.published_selector)
        item = _build_item(
            title,
            link,
            summary,
            summary,
            _parse_datetime(published),
            source.url,
            {"parser": "html", "published_raw": published},
        )
        if item:
            items.append(item)
    unique: dict[str, CrawledItem] = {}
    for item in items:
        unique[item.url] = item
    return list(unique.values())


def _download_dynamic(source: Source) -> bytes:
    validate_public_url(source.url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("html_js sources require the optional Playwright dependency") from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=settings.user_agent)
            page.goto(source.url, wait_until="domcontentloaded", timeout=int(settings.request_timeout_seconds * 1000))
            page.wait_for_timeout(1000)
            body = page.content().encode("utf-8")
        finally:
            browser.close()
    if len(body) > settings.max_response_bytes:
        raise ValueError(f"rendered page is larger than {settings.max_response_bytes} bytes")
    return body


def crawl_source(source: Source) -> list[CrawledItem]:
    if source.kind == "rss":
        body, _content_type = _download(source.url)
        return _parse_rss(body, source.url)
    if source.kind == "html_js":
        return _parse_html(_download_dynamic(source), source)
    body, _content_type = _download(source.url)
    return _parse_html(body, source)
