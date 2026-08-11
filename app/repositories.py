from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import Article, GeneratedImage, ModelArtifact, Setting, Source


logger = logging.getLogger(__name__)


ENV_SETTING_MAP = {
    "ai_chat_endpoint": "AI_CHAT_ENDPOINT",
    "ai_chat_api_key": "AI_CHAT_API_KEY",
    "ai_chat_model": "AI_CHAT_MODEL",
    "ai_image_endpoint": "AI_IMAGE_ENDPOINT",
    "ai_image_api_key": "AI_IMAGE_API_KEY",
    "ai_image_model": "AI_IMAGE_MODEL",
    "image_search_provider": "IMAGE_SEARCH_PROVIDER",
    "preferred_keywords": "PREFERRED_KEYWORDS",
    "blocked_keywords": "BLOCKED_KEYWORDS",
}


def get_setting(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    if row is not None:
        return row.value
    env_name = ENV_SETTING_MAP.get(key)
    if env_name:
        return os.getenv(env_name, default)
    return default


def get_settings(db: Session, keys: Iterable[str]) -> dict[str, str]:
    return {key: get_setting(db, key) for key in keys}


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def list_sources(db: Session) -> list[Source]:
    return list(db.scalars(select(Source).order_by(Source.created_at.desc())))


def get_source(db: Session, source_id: int) -> Source | None:
    return db.get(Source, source_id)


def save_source(db: Session, source: Source) -> Source:
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def delete_source(db: Session, source_id: int) -> None:
    source = db.get(Source, source_id)
    if source is not None:
        db.delete(source)
        db.commit()


def _safe_article_image_path(file_path: str, data_dir: Path) -> Path | None:
    """Resolve a stored media path without allowing deletion outside data_dir."""
    root = data_dir.resolve()
    candidate = (root / str(file_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        logger.warning("Skip article image cleanup outside data directory: %s", file_path)
        return None
    return candidate


def delete_article(db: Session, article_id: int, data_dir: Path) -> bool:
    """Delete an article and its local image files, keeping the source topic."""
    article = db.get(Article, article_id)
    if article is None:
        return False

    stored_paths = list(dict.fromkeys(str(image.file_path) for image in article.images))
    db.delete(article)
    db.commit()

    remaining_paths = set()
    if stored_paths:
        remaining_paths = set(
            db.scalars(select(GeneratedImage.file_path).where(GeneratedImage.file_path.in_(stored_paths))).all()
        )
    image_paths = [
        path
        for file_path in stored_paths
        if file_path not in remaining_paths
        and (path := _safe_article_image_path(file_path, data_dir)) is not None
    ]
    for path in image_paths:
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("Article deleted but image cleanup failed for %s: %s", path, exc)
    return True


def latest_model_artifact(db: Session, name: str) -> ModelArtifact | None:
    return db.scalar(
        select(ModelArtifact)
        .where(ModelArtifact.name == name)
        .order_by(ModelArtifact.trained_at.desc())
        .limit(1)
    )


def delete_old_model_artifacts(db: Session, name: str, keep_path: str) -> None:
    rows = db.scalars(select(ModelArtifact).where(ModelArtifact.name == name)).all()
    for row in rows:
        if row.path != keep_path:
            db.delete(row)
