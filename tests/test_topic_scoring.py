from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.topic_pipeline as topic_pipeline
from app.models import Base, RawItem, Source, Topic, TopicItem


def test_empty_score_batch_does_not_refresh_all_topics(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        topic = Topic(title="不应被空批次触发", summary="摘要", recommendation_score=17)
        db.add(topic)
        db.commit()

        def fail_if_scored(*_args, **_kwargs):
            raise AssertionError("empty score batch must not scan topics")

        monkeypatch.setattr(topic_pipeline, "_calculate_topic_score", fail_if_scored)
        topic_pipeline.refresh_all_topic_scores(db, set())

        assert db.get(Topic, topic.id).recommendation_score == 17


def test_score_refresh_loads_topic_items_in_one_batch():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        first_source = Source(name="来源一", kind="rss", url="https://example.com/one.xml")
        second_source = Source(name="来源二", kind="rss", url="https://example.com/two.xml")
        topic = Topic(title="批量统计热点", summary="摘要")
        db.add_all([first_source, second_source, topic])
        db.flush()
        first_item = RawItem(
            source_id=first_source.id,
            title="批量统计热点",
            normalized_url="https://example.com/one",
            summary="第一条较完整的来源摘要",
            content="第一条内容",
            content_hash="1",
        )
        second_item = RawItem(
            source_id=second_source.id,
            title="批量统计热点跟进",
            normalized_url="https://example.com/two",
            summary="第二条来源摘要",
            content="第二条内容",
            content_hash="2",
        )
        db.add_all([first_item, second_item])
        db.flush()
        db.add_all([
            TopicItem(topic_id=topic.id, raw_item_id=first_item.id),
            TopicItem(topic_id=topic.id, raw_item_id=second_item.id),
        ])
        db.commit()

        topic_pipeline.refresh_all_topic_scores(db, {topic.id})

        refreshed = db.get(Topic, topic.id)
        assert refreshed.item_count == 2
        assert refreshed.source_count == 2
        assert refreshed.content_quality > 0
        assert refreshed.baseline_score >= 0
