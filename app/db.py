from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .categories import categorize_topic
from .models import Base, CrawlLog, CrawlRun, Topic, utcnow
from .source_catalog import ensure_default_source_catalog


engine = create_engine(
    f"sqlite:///{settings.database_path}",
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _ensure_compatible_columns()
    db = SessionLocal()
    try:
        recovered = _recover_incomplete_crawls(db)
        if recovered:
            import logging

            logging.getLogger(__name__).warning("Recovered %s incomplete crawl run(s) as paused", recovered)
        added = ensure_default_source_catalog(db)
        if added:
            import logging

            logging.getLogger(__name__).info("Default Chinese source catalog ready: added=%s", added)
    finally:
        db.close()


def _recover_incomplete_crawls(db: Session) -> int:
    """Turn runs left by a crashed process into resumable paused runs."""
    runs = list(db.scalars(select(CrawlRun).where(CrawlRun.status.in_(["pending", "running"]))))
    for run in runs:
        run.status = "paused"
        run.pause_requested = False
        run.finished_at = utcnow()
        db.add(
            CrawlLog(
                run_id=run.id,
                level="error",
                message="应用上次退出时采集未完成，已自动暂停并保留进度；点击“继续采集”可从未完成的数据源恢复。",
            )
        )
    if runs:
        db.commit()
    return len(runs)


def _ensure_compatible_columns() -> None:
    """Apply small additive SQLite upgrades without requiring a migration service."""
    inspector = inspect(engine)
    additions_by_table = {
        "generated_images": {
            "source_url": "VARCHAR(2000)",
            "attribution": "TEXT",
        },
        "topics": {
            "category": "VARCHAR(50) NOT NULL DEFAULT '其他'",
        },
        "sources": {
            "timeout_seconds": "INTEGER NOT NULL DEFAULT 30",
        },
        "crawl_runs": {
            "selected_source_ids": "JSON",
            "completed_source_ids": "JSON",
            "timeout_seconds": "INTEGER NOT NULL DEFAULT 300",
            "pause_requested": "BOOLEAN NOT NULL DEFAULT 0",
        },
    }
    with engine.begin() as connection:
        tables = set(inspector.get_table_names())
        for table_name, additions in additions_by_table.items():
            if table_name not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for name, sql_type in additions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {sql_type}"))

    # The category is denormalized on purpose: the dashboard can filter and
    # paginate in SQL instead of loading a thousand topics to Python first.
    if "topics" in inspector.get_table_names():
        db = SessionLocal()
        try:
            for topic in db.scalars(select(Topic)).all():
                topic.category = categorize_topic(topic.title, topic.summary, topic.ai_tags)
            db.commit()
        finally:
            db.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def session_scope() -> Session:
    return SessionLocal()
