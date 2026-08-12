import app.crawler as crawler
from app.crawler import _parse_rss
from app.models import Source


def test_parse_rss_returns_real_entry_fields():
    body = b"""
    <rss version='2.0'><channel><title>Source</title>
      <item><title>Real topic title</title><link>https://example.com/item</link>
      <description>A real summary</description><pubDate>Tue, 11 Aug 2026 08:00:00 GMT</pubDate></item>
    </channel></rss>
    """
    items = _parse_rss(body, "https://example.com/feed.xml")
    assert len(items) == 1
    assert items[0].title == "Real topic title"
    assert items[0].url == "https://example.com/item"


def test_crawl_source_uses_configured_timeout_and_round_override(monkeypatch):
    body = b"""
    <rss version='2.0'><channel><title>Source</title>
      <item><title>Real topic title</title><link>https://example.com/item</link>
      <description>A source summary</description></item>
    </channel></rss>
    """
    observed: list[int] = []

    def fake_download(_url: str, timeout_seconds: int | None = None):
        observed.append(timeout_seconds)
        return body, "application/rss+xml"

    monkeypatch.setattr(crawler, "_download", fake_download)
    source = Source(name="bounded", kind="rss", url="https://example.com/feed.xml", timeout_seconds=7)

    crawler.crawl_source(source)
    crawler.crawl_source(source, timeout_seconds=3)

    assert observed == [7, 3]
