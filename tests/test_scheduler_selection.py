from sqlalchemy import create_engine
from contextlib import contextmanager

from sqlalchemy.orm import Session

import app.scheduler as scheduler
from app.models import Base, CrawlRun, Source
from app.scheduler import _sources_for_cycle


def test_crawl_selection_only_returns_enabled_selected_sources():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first = Source(name="科技源", kind="rss", url="https://example.com/tech.xml", enabled=True)
        second = Source(name="财经源", kind="rss", url="https://example.com/money.xml", enabled=True)
        disabled = Source(name="停用源", kind="rss", url="https://example.com/off.xml", enabled=False)
        db.add_all([first, second, disabled])
        db.commit()

        selected = _sources_for_cycle(db, [first.id, disabled.id])

        assert [source.id for source in selected] == [first.id]


def test_scheduled_cycle_returns_all_enabled_sources():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            Source(name="A", kind="rss", url="https://example.com/a.xml", enabled=True),
            Source(name="B", kind="rss", url="https://example.com/b.xml", enabled=False),
        ])
        db.commit()

        assert [source.name for source in _sources_for_cycle(db)] == ["A"]


def test_one_crawl_cycle_never_calls_ai_analysis(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    db.add(Source(name="A", kind="rss", url="https://example.com/a.xml", enabled=True))
    db.commit()

    @contextmanager
    def fake_scope():
        yield db

    called = {"ai": 0}
    monkeypatch.setattr(scheduler, "init_db", lambda: None)
    monkeypatch.setattr(scheduler, "session_scope", fake_scope)
    monkeypatch.setattr(scheduler, "crawl_source", lambda _source: [])
    monkeypatch.setattr(scheduler, "ingest_items", lambda _db, _source, _items: (0, 0))
    monkeypatch.setattr(scheduler, "analyze_pending_topics", lambda _limit=10: called.__setitem__("ai", called["ai"] + 1))

    scheduler.run_crawl_cycle(force=True)

    run = db.query(CrawlRun).one()
    assert run.status == "success"
    assert run.processed_sources == 1
    assert called["ai"] == 0
    db.close()
