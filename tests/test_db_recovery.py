from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import _recover_incomplete_crawls
from app.models import Base, CrawlLog, CrawlRun


def test_incomplete_crawl_is_recovered_as_resumable_pause():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        run = CrawlRun(status="running", total_sources=2, processed_sources=1, completed_source_ids=[1])
        db.add(run)
        db.commit()

        assert _recover_incomplete_crawls(db) == 1

        db.refresh(run)
        assert run.status == "paused"
        assert run.finished_at is not None
        log = db.query(CrawlLog).filter(CrawlLog.run_id == run.id).one()
        assert "继续采集" in log.message
