from app.crawler import _parse_rss


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

