from sqlalchemy import create_engine
from contextlib import contextmanager
from types import SimpleNamespace

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
    monkeypatch.setattr(scheduler, "crawl_source", lambda _source, _timeout=None: [])
    monkeypatch.setattr(scheduler, "ingest_items", lambda _db, _source, _items: (0, 0))
    monkeypatch.setattr(scheduler, "analyze_pending_topics", lambda _limit=10: called.__setitem__("ai", called["ai"] + 1))

    scheduler.run_crawl_cycle(force=True)

    run = db.query(CrawlRun).one()
    assert run.status == "success"
    assert run.processed_sources == 1
    assert called["ai"] == 0
    db.close()


def test_pause_and_resume_continues_only_unfinished_sources(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    first = Source(name="A", kind="rss", url="https://example.com/a.xml", enabled=True)
    second = Source(name="B", kind="rss", url="https://example.com/b.xml", enabled=True)
    db.add_all([first, second])
    db.commit()

    @contextmanager
    def fake_scope():
        yield db

    calls: list[str] = []

    def fake_crawl(source, _timeout=None):
        calls.append(source.name)
        if source.id == first.id:
            run = db.query(CrawlRun).one()
            run.pause_requested = True
            db.commit()
        return []

    monkeypatch.setattr(scheduler, "init_db", lambda: None)
    monkeypatch.setattr(scheduler, "session_scope", fake_scope)
    monkeypatch.setattr(scheduler, "crawl_source", fake_crawl)
    monkeypatch.setattr(scheduler, "ingest_items", lambda _db, _source, _items: (0, 0))

    scheduler.run_crawl_cycle(force=True)

    run = db.query(CrawlRun).one()
    assert run.status == "paused"
    assert run.completed_source_ids == [first.id]
    assert calls == ["A"]

    run.pause_requested = False
    db.commit()
    calls.clear()
    scheduler.run_crawl_cycle(run_id=run.id)

    assert run.status == "success"
    assert run.completed_source_ids == [first.id, second.id]
    assert calls == ["B"]
    db.close()


def test_round_timeout_stops_before_starting_next_source(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    first = Source(name="A", kind="rss", url="https://example.com/a.xml", enabled=True)
    second = Source(name="B", kind="rss", url="https://example.com/b.xml", enabled=True)
    db.add_all([first, second])
    db.commit()
    run = CrawlRun(
        trigger="manual",
        status="pending",
        total_sources=2,
        selected_source_ids=[first.id, second.id],
        completed_source_ids=[],
        timeout_seconds=30,
    )
    db.add(run)
    db.commit()

    @contextmanager
    def fake_scope():
        yield db

    clock_values = iter([0.0, 1.0, 2.0, 100.0])
    calls: list[str] = []

    monkeypatch.setattr(scheduler, "init_db", lambda: None)
    monkeypatch.setattr(scheduler, "session_scope", fake_scope)
    monkeypatch.setattr(scheduler, "time", SimpleNamespace(monotonic=lambda: next(clock_values)))
    monkeypatch.setattr(scheduler, "crawl_source", lambda source, _timeout=None: calls.append(source.name) or [])
    monkeypatch.setattr(scheduler, "ingest_items", lambda _db, _source, _items: (0, 0))

    scheduler.run_crawl_cycle(force=True, run_id=run.id)

    assert run.status == "timeout"
    assert run.processed_sources == 1
    assert calls == ["A"]
    assert any("超时" in log.message for log in run.logs)
    db.close()
