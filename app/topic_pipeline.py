from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .categories import categorize_topic
from .crawler import CrawledItem
from .models import RawItem, Source, Topic, TopicItem, utcnow
from .repositories import get_setting
from .utils import as_utc, clamp, normalize_url, parse_csv_keywords, tokenize


logger = logging.getLogger(__name__)

CONFLICT_TERMS = {"争议", "冲突", "质疑", "回应", "维权", "涨价", "裁员", "处罚", "监管", "道歉", "曝光", "反转", "争论"}
RISK_TERMS = {"未证实", "网传", "谣言", "爆料", "内幕", "涉政", "色情", "赌博", "诈骗"}


def _title_similarity(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    jaccard = intersection / union if union else 0.0
    common = sum(1 for token in left_tokens if token in right_tokens)
    char_overlap = common / max(1, max(len(left_tokens), len(right_tokens)))
    return max(jaccard, char_overlap * 0.8)


def heuristic_conflict(title: str, summary: str) -> float:
    text = f"{title} {summary}"
    hits = sum(1 for term in CONFLICT_TERMS if term in text)
    return clamp(hits * 18.0, 0, 100)


def heuristic_risk(title: str, summary: str) -> float:
    text = f"{title} {summary}"
    hits = sum(1 for term in RISK_TERMS if term in text)
    return clamp(hits * 22.0, 0, 100)


def _topic_candidates(db: Session) -> list[Topic]:
    cutoff = utcnow() - timedelta(days=7)
    return list(db.scalars(select(Topic).where(Topic.last_seen_at >= cutoff).order_by(Topic.last_seen_at.desc()).limit(500)))


def _find_topic(db: Session, title: str) -> Topic | None:
    return _find_topic_from_candidates(_topic_candidates(db), title)


def _find_topic_from_candidates(candidates: list[Topic], title: str) -> Topic | None:
    best: Topic | None = None
    best_score = 0.0
    for candidate in candidates:
        score = _title_similarity(title, candidate.title)
        if score > best_score:
            best = candidate
            best_score = score
    return best if best_score >= 0.34 else None


def ingest_items(
    db: Session,
    source: Source,
    items: list[CrawledItem],
    *,
    refresh_scores: bool = True,
    touched_topic_ids: set[int] | None = None,
) -> tuple[int, int]:
    inserted = 0
    attached = 0
    batch_topic_ids: set[int] = set()
    # Candidate topics are stable for the duration of one source batch.  The
    # old implementation queried and materialized up to 500 topics per item.
    candidates = _topic_candidates(db)
    for item in items:
        normalized_url = normalize_url(item.url, source.url)
        existing = db.scalar(
            select(RawItem).where(
                RawItem.source_id == source.id,
                RawItem.normalized_url == normalized_url,
            )
        )
        if existing is not None:
            continue
        raw = RawItem(
            source_id=source.id,
            title=item.title,
            normalized_url=normalized_url,
            summary=item.summary,
            content=item.content,
            published_at=item.published_at,
            content_hash=item.content_hash,
            metadata_json=item.metadata,
        )
        db.add(raw)
        db.flush()
        topic = _find_topic_from_candidates(candidates, item.title)
        if topic is None:
            topic = Topic(
                title=item.title,
                summary=item.summary or item.content[:500],
                category=categorize_topic(item.title, item.summary),
                first_seen_at=item.published_at or utcnow(),
                last_seen_at=item.published_at or utcnow(),
                conflict_score=heuristic_conflict(item.title, item.summary),
                risk_score=heuristic_risk(item.title, item.summary),
            )
            db.add(topic)
            db.flush()
            candidates.append(topic)
        else:
            topic.last_seen_at = max(as_utc(topic.last_seen_at), as_utc(item.published_at))
            if len(item.summary) > len(topic.summary):
                topic.summary = item.summary
            topic.conflict_score = max(topic.conflict_score, heuristic_conflict(item.title, item.summary))
            topic.risk_score = max(topic.risk_score, heuristic_risk(item.title, item.summary))
            topic.category = categorize_topic(topic.title, topic.summary, topic.ai_tags)
        db.add(TopicItem(topic_id=topic.id, raw_item_id=raw.id))
        batch_topic_ids.add(topic.id)
        inserted += 1
        attached += 1
    source.last_crawled_at = utcnow()
    source.last_success_at = utcnow()
    source.last_error = None
    db.commit()
    if touched_topic_ids is not None:
        touched_topic_ids.update(batch_topic_ids)
    if refresh_scores:
        refresh_all_topic_scores(db, touched_topic_ids if touched_topic_ids is not None else batch_topic_ids)
    return inserted, attached


def mark_source_error(db: Session, source: Source, error: Exception) -> None:
    source.last_crawled_at = utcnow()
    source.last_error = str(error)[:4000]
    db.commit()


def _topic_rows(db: Session, topic: Topic):
    return list(
        db.execute(
            select(RawItem, Source)
            .join(TopicItem, TopicItem.raw_item_id == RawItem.id)
            .join(Source, Source.id == RawItem.source_id)
            .where(TopicItem.topic_id == topic.id)
            .order_by(RawItem.published_at.desc().nullslast(), RawItem.fetched_at.desc())
        )
    )


def _refresh_topic_stats_from_items(topic: Topic, raw_items: list[RawItem], now) -> None:
    if not raw_items:
        return
    topic.item_count = len(raw_items)
    topic.source_count = len({item.source_id for item in raw_items})
    recent_count = sum(1 for item in raw_items if (now - as_utc(item.published_at or item.fetched_at)).total_seconds() <= 6 * 3600)
    topic.source_velocity = recent_count / 6.0
    average_summary_length = sum(len(item.summary or item.content) for item in raw_items) / len(raw_items)
    topic.content_quality = clamp(average_summary_length / 220.0 * 100.0)
    topic.conflict_score = max(
        topic.conflict_score,
        max(heuristic_conflict(item.title, item.summary) for item in raw_items),
    )
    topic.risk_score = max(
        topic.risk_score,
        max(heuristic_risk(item.title, item.summary) for item in raw_items),
    )


def refresh_topic_stats(db: Session, topic: Topic) -> None:
    rows = _topic_rows(db, topic)
    _refresh_topic_stats_from_items(topic, [row[0] for row in rows], utcnow())


def _calculate_topic_score(topic: Topic, keywords: list[str], blocked: list[str], now) -> float:
    age_hours = max(0.0, (now - as_utc(topic.last_seen_at)).total_seconds() / 3600)
    freshness = math.exp(-age_hours / 30.0)
    coverage = min(1.0, topic.source_count / 5.0)
    velocity = min(1.0, topic.source_velocity / 3.0)
    quality = min(1.0, topic.content_quality / 100.0)
    conflict = min(1.0, topic.conflict_score / 100.0)
    topic_tokens = tokenize(f"{topic.title} {topic.summary}")
    preferred_hits = sum(1 for keyword in keywords if keyword in topic_tokens or keyword in topic.title.lower())
    blocked_hits = sum(1 for keyword in blocked if keyword in topic.title.lower() or keyword in topic.summary.lower())
    preference = min(1.0, preferred_hits / max(1, min(3, len(keywords)))) if keywords else 0.0
    score = (
        freshness * 28.0
        + coverage * 22.0
        + velocity * 18.0
        + quality * 12.0
        + conflict * 12.0
        + preference * 8.0
        - min(35.0, topic.risk_score * 0.35)
        - blocked_hits * 25.0
    )
    topic.baseline_score = clamp(score)
    topic.recommendation_score = clamp(
        topic.baseline_score * 0.55 + (topic.model_score or topic.baseline_score) * 0.45
    )
    return topic.recommendation_score


def calculate_baseline_score(db: Session, topic: Topic) -> float:
    refresh_topic_stats(db, topic)
    return _calculate_topic_score(
        topic,
        parse_csv_keywords(get_setting(db, "preferred_keywords")),
        parse_csv_keywords(get_setting(db, "blocked_keywords")),
        utcnow(),
    )


def _topic_raw_items_by_id(db: Session, topic_ids: set[int]) -> dict[int, list[RawItem]]:
    if not topic_ids:
        return {}
    grouped: dict[int, list[RawItem]] = defaultdict(list)
    topic_id_list = list(topic_ids)
    for start in range(0, len(topic_id_list), 500):
        batch_ids = topic_id_list[start : start + 500]
        rows = db.execute(
            select(TopicItem.topic_id, RawItem)
            .join(RawItem, TopicItem.raw_item_id == RawItem.id)
            .where(TopicItem.topic_id.in_(batch_ids))
        )
        for topic_id, raw_item in rows:
            grouped[int(topic_id)].append(raw_item)
    return grouped


def refresh_all_topic_scores(db: Session, topic_ids: set[int] | None = None) -> None:
    # None means a deliberate full refresh; an empty set means this batch did
    # not touch any topics and must not accidentally trigger a full-table scan.
    if topic_ids is not None and not topic_ids:
        return
    statement = select(Topic)
    if topic_ids is not None:
        statement = statement.where(Topic.id.in_(topic_ids))
    topics = list(db.scalars(statement))
    if not topics:
        return
    raw_items_by_topic = _topic_raw_items_by_id(db, {topic.id for topic in topics})
    keywords = parse_csv_keywords(get_setting(db, "preferred_keywords"))
    blocked = parse_csv_keywords(get_setting(db, "blocked_keywords"))
    now = utcnow()
    for topic in topics:
        _refresh_topic_stats_from_items(topic, raw_items_by_topic.get(topic.id, []), now)
        _calculate_topic_score(topic, keywords, blocked, now)
    db.commit()


def refresh_topic_score_mix(db: Session, topic_ids: set[int] | None = None) -> None:
    """Rebuild only the baseline/model blend after model predictions change."""
    if topic_ids is not None and not topic_ids:
        return
    if topic_ids is None:
        topics = list(db.scalars(select(Topic)))
    else:
        topics = []
        topic_id_list = list(topic_ids)
        for start in range(0, len(topic_id_list), 500):
            batch_ids = topic_id_list[start : start + 500]
            topics.extend(db.scalars(select(Topic).where(Topic.id.in_(batch_ids))))
    for topic in topics:
        topic.recommendation_score = clamp(
            topic.baseline_score * 0.55 + (topic.model_score or topic.baseline_score) * 0.45
        )
    db.commit()


def topic_sources(db: Session, topic_id: int):
    topic = db.get(Topic, topic_id)
    return _topic_rows(db, topic) if topic else []
