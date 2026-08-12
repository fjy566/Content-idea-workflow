from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from .ai_provider import AIProvider, AIProviderError
from .config import settings
from .crawler import crawl_source
from .db import init_db, session_scope
from .models import AITask, CrawlLog, CrawlRun, Source, Topic, utcnow
from .repositories import get_setting
from .topic_pipeline import calculate_baseline_score, ingest_items, mark_source_error, topic_sources
from .topic_recommendations import normalize_recommendations
from .utils import as_utc, clean_text


logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _save_analysis(db, topic: Topic, result: dict, usage: dict) -> None:
    topic.ai_summary = clean_text(result.get("summary"), 5000)
    topic.ai_conflict = clean_text(result.get("conflict"), 2000)
    audience = result.get("audience") or []
    tags = result.get("tags") or []
    topic.ai_audience = [clean_text(value, 200) for value in audience if clean_text(value, 200)][:10]
    topic.ai_angles = normalize_recommendations(result.get("angles"), required_count=5)
    topic.ai_tags = [clean_text(value, 100) for value in tags if clean_text(value, 100)][:20]
    topic.ai_risk_level = clean_text(result.get("risk_level"), 30).lower() or "medium"
    topic.ai_confidence = float(result.get("confidence", 0) or 0)
    topic.content_quality = max(topic.content_quality, min(100.0, float(result.get("content_potential", 0) or 0)))
    topic.risk_score = {"low": 15.0, "medium": 45.0, "high": 85.0}.get(topic.ai_risk_level, topic.risk_score)
    topic.ai_error = None
    topic.analyzed_at = utcnow()
    calculate_baseline_score(db, topic)


def analyze_pending_topics(limit: int = 10) -> int:
    with session_scope() as db:
        provider = AIProvider.from_db(db)
        if not provider.chat_config.configured:
            logger.info("Skipping AI analysis: chat API is not configured")
            return 0
        cutoff = utcnow() - timedelta(hours=12)
        topics = list(
            db.scalars(
                select(Topic)
                .where((Topic.analyzed_at.is_(None)) | (Topic.analyzed_at < cutoff))
                .order_by(Topic.recommendation_score.desc(), Topic.last_seen_at.desc())
                .limit(limit)
            )
        )
        completed = 0
        for topic in topics:
            task = AITask(task_type="analyze_topic", status="running", model=provider.chat_config.model)
            db.add(task)
            db.commit()
            try:
                result, usage = provider.analyze_topic(topic, topic_sources(db, topic.id), get_setting(db, "preferred_keywords"))
                _save_analysis(db, topic, result, usage)
                task.status = "success"
                task.usage_json = usage
                completed += 1
            except (AIProviderError, ValueError, TypeError) as exc:
                topic.ai_error = str(exc)
                task.status = "failed"
                task.error_message = str(exc)[:4000]
                logger.warning("Topic analysis failed for %s: %s", topic.id, exc)
            task.finished_at = utcnow()
            db.commit()
        return completed


def _sources_for_cycle(db, source_ids: list[int] | None = None) -> list[Source]:
    statement = select(Source).where(Source.enabled.is_(True))
    if source_ids is not None:
        statement = statement.where(Source.id.in_(source_ids))
    return list(db.scalars(statement.order_by(Source.name.asc())))


def _round_timeout(value: object | None) -> int:
    try:
        seconds = int(value or settings.crawl_round_timeout_seconds)
    except (TypeError, ValueError):
        seconds = settings.crawl_round_timeout_seconds
    return max(30, min(3600, seconds))


def _source_ids_from_run(run: CrawlRun, source_ids: list[int] | None) -> list[int] | None:
    values = source_ids
    if values is None and isinstance(run.selected_source_ids, list):
        values = run.selected_source_ids
    if values is None:
        return None
    result: list[int] = []
    for value in values:
        try:
            source_id = int(value)
        except (TypeError, ValueError):
            continue
        if source_id not in result:
            result.append(source_id)
    return result


def _completed_source_ids(run: CrawlRun) -> set[int]:
    if not isinstance(run.completed_source_ids, list):
        return set()
    result: set[int] = set()
    for value in run.completed_source_ids:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _crawl_log(db, run_id: int, message: str, source_name: str | None = None, level: str = "info") -> None:
    db.add(CrawlLog(run_id=run_id, source_name=source_name, level=level, message=clean_text(message, 2000)))
    db.commit()


def run_crawl_cycle(
    force: bool = False,
    source_ids: list[int] | None = None,
    run_id: int | None = None,
    trigger: str = "scheduled",
) -> dict[str, int]:
    """Run one bounded pass over selected sources; never invokes a paid AI API."""
    init_db()
    result = {"sources": 0, "items": 0, "errors": 0}
    with session_scope() as db:
        run = db.get(CrawlRun, run_id) if run_id else None
        effective_source_ids = _source_ids_from_run(run, source_ids) if run is not None else source_ids
        sources = _sources_for_cycle(db, effective_source_ids)
        if run is None:
            active = db.scalar(select(CrawlRun).where(CrawlRun.status.in_(["pending", "running", "paused"])).limit(1))
            if active is not None:
                logger.info("Skipping crawl: run %s is already active", active.id)
                return result
            run = CrawlRun(
                trigger=trigger,
                status="pending",
                total_sources=len(sources),
                selected_source_ids=effective_source_ids,
                completed_source_ids=[],
                timeout_seconds=settings.crawl_round_timeout_seconds,
            )
            db.add(run)
            db.commit()
            db.refresh(run)
        elif run.status not in {"pending", "running", "paused"}:
            logger.info("Skipping crawl: run %s is already %s", run.id, run.status)
            return result
        elif run.selected_source_ids is None and effective_source_ids is not None:
            run.selected_source_ids = effective_source_ids

        completed_ids = _completed_source_ids(run)
        if run.total_sources <= 0:
            run.total_sources = len(sources)
        run.status = "running"
        run.started_at = utcnow()
        run.finished_at = None
        db.commit()
        _crawl_log(db, run.id, f"开始单轮采集，共 {len(sources)} 个数据源；本轮不会自动调用 AI。")

        deadline = time.monotonic() + _round_timeout(run.timeout_seconds)
        for source in sources:
            if source.id in completed_ids:
                continue
            if run.pause_requested:
                run.status = "paused"
                run.finished_at = utcnow()
                db.commit()
                _crawl_log(db, run.id, "已按请求暂停：当前源已完成，保留已完成进度。")
                return result
            if time.monotonic() >= deadline:
                run.status = "timeout"
                run.finished_at = utcnow()
                db.commit()
                _crawl_log(db, run.id, f"本轮采集超时：达到设定的 {_round_timeout(run.timeout_seconds)} 秒总时限。", level="error")
                return result

            result["sources"] += 1
            if not force and source.last_crawled_at is not None:
                elapsed = utcnow() - as_utc(source.last_crawled_at)
                if elapsed.total_seconds() < max(5, source.interval_minutes) * 60:
                    completed_ids.add(source.id)
                    run.completed_source_ids = sorted(completed_ids)
                    run.processed_sources = len(completed_ids)
                    db.commit()
                    _crawl_log(db, run.id, "尚未到该数据源的采集间隔，本轮跳过。", source.name)
                    continue
            try:
                _crawl_log(db, run.id, "开始采集。", source.name)
                remaining = max(1, int(deadline - time.monotonic()))
                items = crawl_source(source, remaining)
                inserted, _attached = ingest_items(db, source, items)
                result["items"] += inserted
                run.new_items += inserted
                _crawl_log(db, run.id, f"完成：解析 {len(items)} 条，新增 {inserted} 条。", source.name)
                logger.info("Crawled %s: %s parsed, %s new", source.name, len(items), inserted)
            except Exception as exc:
                result["errors"] += 1
                run.error_count += 1
                logger.exception("Crawl failed for %s", source.name)
                mark_source_error(db, source, exc)
                _crawl_log(db, run.id, f"失败：{exc}", source.name, "error")

            completed_ids.add(source.id)
            run.completed_source_ids = sorted(completed_ids)
            run.processed_sources = len(completed_ids)
            db.commit()

        remaining_sources = any(source.id not in completed_ids for source in sources)
        if run.pause_requested and remaining_sources:
            run.status = "paused"
            run.finished_at = utcnow()
            db.commit()
            _crawl_log(db, run.id, "已按请求暂停：已完成当前源，等待继续采集。")
        else:
            run.status = "partial" if run.error_count else "success"
            run.finished_at = utcnow()
            db.commit()
            _crawl_log(db, run.id, f"本轮结束：新增 {run.new_items} 条，失败 {run.error_count} 个数据源。")
    return result


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        run_crawl_cycle,
        "interval",
        minutes=settings.crawl_interval_minutes,
        id="crawl-cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(ZoneInfo("Asia/Shanghai")) + timedelta(seconds=5),
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
