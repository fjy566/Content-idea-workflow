from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str
    host: str
    port: int
    data_dir: Path
    crawl_interval_minutes: int
    source_timeout_seconds: int
    crawl_round_timeout_seconds: int
    request_timeout_seconds: float
    max_response_bytes: int
    user_agent: str

    @property
    def database_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models"


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", "data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "images").mkdir(parents=True, exist_ok=True)
    (data_dir / "models").mkdir(parents=True, exist_ok=True)
    return Settings(
        app_name=os.getenv("APP_NAME", "Content Idea Workflow"),
        host=os.getenv("HOST", "127.0.0.1"),
        port=_int_env("PORT", 8000),
        data_dir=data_dir,
        crawl_interval_minutes=max(5, _int_env("CRAWL_INTERVAL_MINUTES", 30)),
        source_timeout_seconds=max(5, min(300, _int_env("SOURCE_TIMEOUT_SECONDS", 30))),
        crawl_round_timeout_seconds=max(30, min(3600, _int_env("CRAWL_ROUND_TIMEOUT_SECONDS", 300))),
        request_timeout_seconds=max(5.0, _float_env("REQUEST_TIMEOUT_SECONDS", 25.0)),
        max_response_bytes=max(100_000, _int_env("MAX_RESPONSE_BYTES", 5_000_000)),
        user_agent=os.getenv(
            "USER_AGENT",
            "ContentIdeaWorkflow/0.1 (+local personal research tool)",
        ),
    )


settings = load_settings()
