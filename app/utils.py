from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import jieba


TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def clean_text(value: object | None, max_length: int = 20_000) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:max_length]


def normalize_url(url: str, base_url: str | None = None) -> str:
    result = urljoin(base_url or "", clean_text(url))
    parts = urlsplit(result)
    if not parts.scheme or not parts.netloc:
        return result
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_KEYS:
            continue
        query.append((key, value))
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def content_hash(*values: str) -> str:
    joined = "\n".join(clean_text(value).lower() for value in values)
    return hashlib.sha256(joined.encode("utf-8", errors="ignore")).hexdigest()


def tokenize(text: str) -> set[str]:
    words = set()
    for token in jieba.lcut(clean_text(text).lower(), cut_all=False):
        token = token.strip()
        if not token or re.fullmatch(r"[\W_]+", token):
            continue
        if len(token) > 1 or re.search(r"[\u4e00-\u9fff]", token):
            words.add(token)
    return words


def safe_filename(name: str, suffix: str = "") -> str:
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "file"
    return f"{stem[:80]}{suffix}"


def parse_csv_keywords(value: str) -> list[str]:
    return [item.strip().lower() for item in re.split(r"[,，\n]", value or "") if item.strip()]


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def relative_file_path(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
