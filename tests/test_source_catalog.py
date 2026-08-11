from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Base, Source
from app.source_catalog import ensure_china_source_catalog, is_china_source_url, seed_china_sources


def test_source_allowlist_accepts_chinese_domains_and_rejects_foreign_domains():
    assert is_china_source_url("https://www.chinanews.com.cn/rss/scroll-news.xml")
    assert is_china_source_url("https://www.geekpark.net/rss")
    assert not is_china_source_url("https://feeds.bbci.co.uk/news/world/rss.xml")
    assert not is_china_source_url("https://techcrunch.com/feed/")


def test_catalog_removes_foreign_sources_and_seeds_real_chinese_feeds():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all(
            [
                Source(name="BBC", url="https://feeds.bbci.co.uk/news/world/rss.xml"),
                Source(name="中国新闻网", url="https://www.chinanews.com.cn/rss/china.xml"),
            ]
        )
        db.commit()
        removed, added = ensure_china_source_catalog(db)

        urls = {source.url for source in db.scalars(select(Source)).all()}
        assert removed == 1
        assert added >= 1
        assert "https://feeds.bbci.co.uk/news/world/rss.xml" not in urls
        assert "https://www.geekpark.net/rss" in urls
        assert all(is_china_source_url(url) for url in urls)

        assert seed_china_sources(db) == 0
