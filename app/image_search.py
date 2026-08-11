from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from bs4 import BeautifulSoup

from .config import settings
from .security import validate_public_url
from .utils import clean_text, safe_filename


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
QIHOO_IMAGE_API = "https://image.so.com/j"
DEFAULT_IMAGE_SEARCH_PROVIDER = "360"
IMAGE_SEARCH_PROVIDER_LABELS = {
    "360": "360 图片（中国搜索）",
}
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
SEARCH_TRANSLATIONS = {
    "人工智能": "artificial intelligence",
    "大模型": "large language model",
    "芯片": "semiconductor chip",
    "半导体": "semiconductor",
    "机器人": "robotics",
    "手机": "smartphone",
    "新能源汽车": "electric vehicle",
    "汽车": "automobile",
    "航天": "spaceflight",
    "科技": "technology",
    "互联网": "internet technology",
    "医疗": "healthcare",
    "经济": "economy",
    "金融": "finance",
}


@dataclass(slots=True)
class CommonsImage:
    file_path: Path
    source_url: str
    attribution: str
    title: str
    provider: str = "Wikimedia Commons"
    fallback_reason: str = ""


def _metadata_text(metadata: dict, key: str, limit: int = 500) -> str:
    raw = metadata.get(key, {})
    value = raw.get("value", "") if isinstance(raw, dict) else raw
    plain = BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    return clean_text(plain, limit)


def _query_candidates(query: str) -> list[str]:
    translated = [english for chinese, english in SEARCH_TRANSLATIONS.items() if chinese in query]
    candidates: list[str] = []
    if translated:
        unique_translations = list(dict.fromkeys(translated))
        candidates.extend(unique_translations)
        candidates.append(query)
        if len(unique_translations) > 1:
            candidates.append(" ".join(unique_translations))
    else:
        candidates.append(query)
    candidates.append("China technology" if any(word in query for word in ("科技", "芯片", "AI", "人工智能")) else "China news")
    return list(dict.fromkeys(candidates))


def search_commons_image(query: str, exclude_source_urls: set[str] | None = None) -> CommonsImage:
    """Find and locally save the first usable, attributable Commons raster image."""
    query = clean_text(query, 200)
    if len(query) < 2:
        raise ValueError("图片搜索词至少需要 2 个字符")
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "generator": "search",
        "gsrnamespace": "6",
        "gsrlimit": "10",
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "iiurlwidth": "1600",
        "uselang": "zh-cn",
    }
    headers = {"User-Agent": settings.user_agent, "Accept": "application/json"}
    excluded = exclude_source_urls or set()
    try:
        with httpx.Client(timeout=settings.request_timeout_seconds, follow_redirects=True, headers=headers) as client:
            for candidate in _query_candidates(query):
                response = client.get(COMMONS_API, params={**params, "gsrsearch": candidate})
                response.raise_for_status()
                pages = response.json().get("query", {}).get("pages", [])
                for page in pages:
                    info_rows = page.get("imageinfo") or []
                    if not info_rows:
                        continue
                    info = info_rows[0]
                    mime = clean_text(info.get("mime"), 100).lower()
                    if mime not in ALLOWED_IMAGE_TYPES:
                        continue
                    image_url = info.get("thumburl") or info.get("url")
                    source_url = info.get("descriptionurl")
                    if not image_url or not source_url or source_url in excluded:
                        continue
                    validate_public_url(image_url)
                    validate_public_url(source_url)
                    image_response = client.get(image_url)
                    image_response.raise_for_status()
                    validate_public_url(str(image_response.url))
                    actual_type = image_response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if actual_type not in ALLOWED_IMAGE_TYPES:
                        continue
                    content_length = image_response.headers.get("content-length")
                    try:
                        if content_length and int(content_length) > settings.max_response_bytes:
                            continue
                    except ValueError:
                        continue
                    body = image_response.content
                    if not body or len(body) > settings.max_response_bytes:
                        continue
                    metadata = info.get("extmetadata") or {}
                    artist = _metadata_text(metadata, "Artist") or _metadata_text(metadata, "Credit") or "Wikimedia Commons 贡献者"
                    license_name = _metadata_text(metadata, "LicenseShortName") or "请在原始页面核对许可"
                    attribution = clean_text(f"作者/来源：{artist}；许可：{license_name}", 1500)
                    filename = safe_filename(uuid4().hex, ALLOWED_IMAGE_TYPES[actual_type])
                    path = settings.image_dir / filename
                    path.write_bytes(body)
                    return CommonsImage(
                        file_path=path,
                        source_url=clean_text(source_url, 2000),
                        attribution=attribution,
                        title=clean_text(page.get("title"), 500),
                    )
    except (httpx.HTTPError, KeyError, TypeError) as exc:
        raise ValueError(f"Wikimedia Commons 请求失败：{exc}") from exc
    raise ValueError("Wikimedia Commons 没有找到可安全下载的 JPG、PNG 或 WebP 图片，请换一个搜索词")


def _qihoo_source_url(item: dict, image_url: str) -> str:
    """Prefer a source page, but keep each result deduplicatable when 360 has no page."""
    link = clean_text(item.get("link"), 2000)
    if link:
        try:
            validate_public_url(link)
            host = (link.split("/", 3)[2] if "/" in link else "").lower()
            if host and host not in {"image.so.com", "pic.360.com", "so.com"}:
                return link
        except ValueError:
            pass
    return image_url


def search_360_image(query: str, exclude_source_urls: set[str] | None = None) -> CommonsImage:
    """Search 360 图片 by keyword and save the first safe raster result locally.

    This endpoint is the public JSON result feed used by image.so.com, not an
    authenticated 360 cloud SDK. Keep the adapter small and fail closed so a
    changed or blocked feed can use the configured fallback.
    """
    query = clean_text(query, 200)
    if len(query) < 2:
        raise ValueError("图片搜索词至少需要 2 个字符")
    excluded = exclude_source_urls or set()
    params = {"q": query, "pn": 40, "sn": 0}
    headers = {"User-Agent": settings.user_agent, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=settings.request_timeout_seconds, follow_redirects=True, headers=headers) as client:
            response = client.get(QIHOO_IMAGE_API, params=params)
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("list", []) if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                rows = []
            seen_urls: set[str] = set()
            for item in rows:
                if not isinstance(item, dict):
                    continue
                image_url = clean_text(item.get("thumb") or item.get("thumb_bak") or item.get("https") or item.get("img"), 2000)
                if image_url.startswith("//"):
                    image_url = f"https:{image_url}"
                elif image_url.startswith("http://"):
                    image_url = f"https://{image_url[7:]}"
                if not image_url or image_url in seen_urls:
                    continue
                seen_urls.add(image_url)
                try:
                    validate_public_url(image_url)
                    image_response = client.get(image_url)
                    image_response.raise_for_status()
                    validate_public_url(str(image_response.url))
                    actual_type = image_response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if actual_type not in ALLOWED_IMAGE_TYPES:
                        continue
                    content_length = image_response.headers.get("content-length")
                    if content_length and int(content_length) > settings.max_response_bytes:
                        continue
                    body = image_response.content
                    if not body or len(body) > settings.max_response_bytes:
                        continue
                    source_url = _qihoo_source_url(item, image_url)
                    if source_url in excluded:
                        continue
                    attribution = clean_text(
                        "来源：360 图片搜索；原始页面/图片地址请在发布前核对版权",
                        1500,
                    )
                    filename = safe_filename(uuid4().hex, ALLOWED_IMAGE_TYPES[actual_type])
                    path = settings.image_dir / filename
                    path.write_bytes(body)
                    return CommonsImage(
                        file_path=path,
                        source_url=source_url,
                        attribution=attribution,
                        title=clean_text(item.get("title") or query, 500),
                        provider=IMAGE_SEARCH_PROVIDER_LABELS["360"],
                    )
                except (httpx.HTTPError, OSError, TypeError, ValueError, OverflowError):
                    continue
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ValueError(f"360 图片搜索请求失败：{exc}") from exc
    raise ValueError("360 图片没有找到可安全下载的 JPG、PNG 或 WebP 图片，请换一个搜索词")


def search_image(
    query: str,
    exclude_source_urls: set[str] | None = None,
    provider: str = DEFAULT_IMAGE_SEARCH_PROVIDER,
) -> CommonsImage:
    """Search the configured Chinese keyword engine only.

    The platform intentionally does not silently switch to a foreign image
    source.  A failed Chinese search is surfaced to the user so the query can
    be refined or the image can be generated through the user's own API.
    """
    selected = clean_text(provider, 40).lower() or DEFAULT_IMAGE_SEARCH_PROVIDER
    excluded = exclude_source_urls or set()
    if selected != DEFAULT_IMAGE_SEARCH_PROVIDER:
        raise ValueError(f"当前仅支持中国图片搜索源：{selected}")
    return search_360_image(query, excluded)


def image_search_label(provider: str | None) -> str:
    return IMAGE_SEARCH_PROVIDER_LABELS.get(clean_text(provider, 40).lower(), IMAGE_SEARCH_PROVIDER_LABELS[DEFAULT_IMAGE_SEARCH_PROVIDER])
