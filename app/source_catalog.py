from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting, Source, Topic, utcnow


@dataclass(frozen=True, slots=True)
class SourcePreset:
    name: str
    url: str
    kind: str = "rss"
    interval_minutes: int = 30


# These are public Chinese publisher feeds.  We deliberately keep the catalog
# small and verified instead of silently adding arbitrary aggregators.
CHINA_SOURCE_PRESETS: tuple[SourcePreset, ...] = (
    SourcePreset("中国新闻网-滚动", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    SourcePreset("中国新闻网-国内", "https://www.chinanews.com.cn/rss/china.xml"),
    SourcePreset("中国新闻网-社会", "https://www.chinanews.com.cn/rss/society.xml"),
    SourcePreset("中国新闻网-财经", "https://www.chinanews.com.cn/rss/finance.xml"),
    SourcePreset("IT之家", "https://www.ithome.com/rss/"),
    SourcePreset("少数派", "https://sspai.com/feed"),
    SourcePreset("爱范儿", "https://www.ifanr.com/feed"),
    SourcePreset("博客园-站点首页", "https://feed.cnblogs.com/blog/sitehome/rss"),
    SourcePreset("极客公园", "https://www.geekpark.net/rss"),
    SourcePreset("量子位", "https://www.qbitai.com/feed"),
    SourcePreset("钛媒体", "https://www.tmtpost.com/feed"),
)


# China-based publishers often use .com rather than .cn.  This allowlist lets
# users add other Chinese feeds while preventing foreign sources from coming
# back through the source form or a stale database row.
CHINA_SOURCE_HOSTS = frozenset(
    {
        "chinanews.com.cn",
        "ithome.com",
        "sspai.com",
        "ifanr.com",
        "cnblogs.com",
        "geekpark.net",
        "qbitai.com",
        "tmtpost.com",
        "36kr.com",
        "huxiu.com",
        "jiemian.com",
        "pingwest.com",
        "leiphone.com",
        "donews.com",
    }
)

CHINA_SOURCE_MIGRATION_KEY = "china_source_catalog_v2"


def source_hostname(url: str) -> str:
    try:
        return (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def is_china_source_url(url: str) -> bool:
    host = source_hostname(url)
    if not host:
        return False
    if host.endswith(".cn") or host.endswith(".com.cn"):
        return True
    return host in CHINA_SOURCE_HOSTS or any(host.endswith(f".{domain}") for domain in CHINA_SOURCE_HOSTS)


def _source_key(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _seed_missing_sources(db: Session) -> int:
    existing = {_source_key(source.url) for source in db.scalars(select(Source)).all()}
    added = 0
    for preset in CHINA_SOURCE_PRESETS:
        key = _source_key(preset.url)
        if key in existing:
            continue
        db.add(
            Source(
                name=preset.name,
                kind=preset.kind,
                url=preset.url,
                interval_minutes=preset.interval_minutes,
                enabled=True,
            )
        )
        existing.add(key)
        added += 1
    return added


def _reset_orphan_topic_stats(db: Session) -> None:
    """Keep article history, but stop source-less historical topics entering the radar."""
    for topic in db.scalars(select(Topic).where(~Topic.item_links.any())).all():
        topic.item_count = 0
        topic.source_count = 0
        topic.source_velocity = 0.0


def ensure_china_source_catalog(db: Session) -> tuple[int, int]:
    """Remove foreign sources and seed the verified Chinese catalog once.

    The app is a local single-user tool, so a startup migration is simpler and
    more reliable than requiring a separate migration command.  Existing
    articles and topics are preserved; only foreign source rows and their raw
    crawl items are removed.
    """
    removed = 0
    for source in db.scalars(select(Source)).all():
        if not is_china_source_url(source.url):
            db.delete(source)
            removed += 1
    db.flush()

    marker = db.get(Setting, CHINA_SOURCE_MIGRATION_KEY)
    added = _seed_missing_sources(db) if marker is None else 0
    if marker is None:
        db.add(Setting(key=CHINA_SOURCE_MIGRATION_KEY, value=utcnow().isoformat()))
    image_provider = db.get(Setting, "image_search_provider")
    if image_provider is not None and image_provider.value != "360":
        image_provider.value = "360"
    _reset_orphan_topic_stats(db)
    db.commit()
    return removed, added


def seed_china_sources(db: Session) -> int:
    """Restore any missing curated Chinese feeds from the Sources page."""
    added = _seed_missing_sources(db)
    db.commit()
    return added
