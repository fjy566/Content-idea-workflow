from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .categories import categorize_topic
from .models import Base, Topic
from .source_catalog import ensure_china_source_catalog


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
        removed, added = ensure_china_source_catalog(db)
        if removed or added:
            import logging

            logging.getLogger(__name__).info("China source catalog ready: removed=%s added=%s", removed, added)
    finally:
        db.close()


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
