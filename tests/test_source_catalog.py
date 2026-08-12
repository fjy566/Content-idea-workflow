from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.routes.sources as source_routes
from app.models import Base, Setting, Source
from app.source_catalog import (
    CHINA_SOURCE_PRESETS,
    DEFAULT_SOURCE_CATALOG_KEY,
    LEGACY_CHINA_SOURCE_MIGRATION_KEY,
    ensure_default_source_catalog,
    is_china_source_url,
    seed_china_sources,
)


def test_source_allowlist_accepts_chinese_domains_and_rejects_foreign_domains():
    assert is_china_source_url("https://www.chinanews.com.cn/rss/scroll-news.xml")
    assert is_china_source_url("https://www.geekpark.net/rss")
    assert not is_china_source_url("https://feeds.bbci.co.uk/news/world/rss.xml")
    assert not is_china_source_url("https://techcrunch.com/feed/")


def test_catalog_contains_verified_chinese_it_feeds():
    urls = {preset.url for preset in CHINA_SOURCE_PRESETS}

    assert "https://www.infoq.cn/feed" in urls
    assert "https://www.oschina.net/news/rss" in urls
    assert "https://www.v2ex.com/index.xml" in urls
    assert "https://segmentfault.com/feeds" in urls
    assert "https://www.leiphone.com/feed" in urls
    assert "https://www.woshipm.com/feed" in urls
    assert all(is_china_source_url(url) for url in urls)


def test_fresh_install_seeds_real_chinese_defaults():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        added = ensure_default_source_catalog(db)

        urls = {source.url for source in db.scalars(select(Source)).all()}
        assert added >= 1
        assert "https://www.geekpark.net/rss" in urls
        assert all(is_china_source_url(url) for url in urls)
        assert db.get(Setting, DEFAULT_SOURCE_CATALOG_KEY) is not None

        assert seed_china_sources(db) == 0


def test_startup_preserves_every_existing_source_without_adding_defaults():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        foreign_url = "https://feeds.bbci.co.uk/news/world/rss.xml"
        db.add(Source(name="用户自定义来源", url=foreign_url))
        db.commit()

        assert ensure_default_source_catalog(db) == 0
        urls = {source.url for source in db.scalars(select(Source)).all()}
        assert urls == {foreign_url}


def test_legacy_installation_with_empty_sources_stays_empty():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Setting(key=LEGACY_CHINA_SOURCE_MIGRATION_KEY, value="completed"))
        db.commit()

        assert ensure_default_source_catalog(db) == 0
        assert list(db.scalars(select(Source)).all()) == []


def test_manual_restore_adds_chinese_presets_without_deleting_custom_source():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        foreign_url = "https://feeds.bbci.co.uk/news/world/rss.xml"
        db.add(Source(name="用户自定义来源", url=foreign_url))
        db.commit()

        assert seed_china_sources(db) >= 1
        urls = {source.url for source in db.scalars(select(Source)).all()}
        assert foreign_url in urls
        assert "https://www.geekpark.net/rss" in urls


def test_user_can_add_a_public_custom_source_regardless_of_region(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(source_routes, "validate_public_url", lambda _url: None)
    with Session(engine) as db:
        response = source_routes.add_source(
            name="我的自定义来源",
            kind="rss",
            url="https://feeds.bbci.co.uk/news/world/rss.xml",
            interval_minutes=30,
            item_selector="",
            title_selector="",
            link_selector="",
            summary_selector="",
            published_selector="",
            db=db,
        )

        assert response.status_code == 303
        assert db.scalar(select(Source).where(Source.name == "我的自定义来源")) is not None
