from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.models import AITask, Base, Topic
from app.routes.topics import analyze_topic
from app.web import templates


def _topic(**overrides):
    values = {
        "id": 7,
        "title": "国产 AI 芯片出现新进展",
        "summary": "<p>这是一段来自真实来源的摘要，用来说明事件背景。</p>",
        "recommendation_score": 82.0,
        "source_count": 3,
        "item_count": 4,
        "conflict_score": 35.0,
        "last_seen_at": datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc),
        "analyzed_at": None,
        "ai_error": None,
        "ai_summary": None,
        "ai_conflict": None,
        "ai_audience": [],
        "ai_angles": [],
        "ai_tags": [],
        "ai_risk_level": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_dashboard_topic_is_a_compact_row():
    html = templates.env.get_template("partials/topic_card.html").render(topic=_topic())

    assert 'class="topic-row"' in html
    assert "这是一段来自真实来源的摘要" in html
    assert "<p><p>" not in html
    assert "</p></p>" not in html
    assert "争议" not in html


def test_detail_guides_unconfigured_user_to_settings():
    html = templates.env.get_template("topic.html").render(
        topic=_topic(),
        source_rows=[],
        chat_configured=False,
        chat_model="",
        notice="",
    )

    assert "AI 选题推荐" in html
    assert "去设置 API" in html
    assert 'action="/topics/7/analyze"' not in html


def test_detail_shows_generate_action_when_api_is_ready():
    recommendations = [{"title": "选题A", "approach": "切入点A", "reader_value": "读者价值A"}]
    html = templates.env.get_template("topic.html").render(
        topic=_topic(),
        source_rows=[],
        chat_configured=True,
        chat_model="deepseek-chat",
        notice="",
        article_error="",
        recommendations=recommendations,
        article_types=("热点评论",),
        article_styles=("清晰克制",),
        article_lengths=("1200",),
    )

    assert "生成选题推荐" in html
    assert 'action="/topics/7/analyze"' in html
    assert 'data-loading-title="正在生成文章初稿"' in html
    assert "生成后自动保存到文章库" in html
    assert "选题A" in html
    assert "切入点A" in html
    assert "读者价值A" in html
    assert 'value="recommendation:0"' in html
    assert "我自己定义选题" in html


def test_missing_api_does_not_create_failed_task():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Topic(id=7, title="真实热点", summary="真实来源摘要"))
        db.commit()

        response = analyze_topic(7, db)

        assert response.status_code == 303
        assert response.headers["location"] == "/topics/7?notice=api_missing"
        assert db.scalar(select(func.count()).select_from(AITask)) == 0
