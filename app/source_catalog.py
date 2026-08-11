from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Setting, Source, utcnow


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


# China-based publishers often use .com rather than .cn.  This list is useful
# for catalog checks and tests, but custom user sources are not restricted to it.
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

LEGACY_CHINA_SOURCE_MIGRATION_KEY = "china_source_catalog_v2"
DEFAULT_SOURCE_CATALOG_KEY = "default_source_catalog_v3"


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


def ensure_default_source_catalog(db: Session) -> int:
    """Seed Chinese defaults only for a genuinely fresh installation.

    Startup must never remove, disable, or rewrite a source chosen by the user.
    Existing installations from the previous catalog migration are also left
    untouched, including installations where the user intentionally removed
    every source.
    """
    marker = db.get(Setting, DEFAULT_SOURCE_CATALOG_KEY)
    added = 0
    if marker is None:
        legacy_marker = db.get(Setting, LEGACY_CHINA_SOURCE_MIGRATION_KEY)
        has_sources = db.scalar(select(Source.id).limit(1)) is not None
        if legacy_marker is None and not has_sources:
            added = _seed_missing_sources(db)
        db.add(Setting(key=DEFAULT_SOURCE_CATALOG_KEY, value=utcnow().isoformat()))
    image_provider = db.get(Setting, "image_search_provider")
    if image_provider is not None and image_provider.value != "360":
        image_provider.value = "360"
    db.commit()
    return added


def seed_china_sources(db: Session) -> int:
    """Restore any missing curated Chinese feeds from the Sources page."""
    added = _seed_missing_sources(db)
    db.commit()
    return added
