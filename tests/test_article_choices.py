from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

import app.routes.topics as topic_routes
from app.image_search import CommonsImage
from app.models import AITask, Article, Base, GeneratedImage, Topic
from app.routes.topics import generate_article, generate_image


def _db_with_topic() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add(
        Topic(
            id=9,
            title="一个真实热点",
            summary="真实摘要",
            ai_angles=[{"title": "推荐A", "approach": "切入A", "reader_value": "价值A"}],
        )
    )
    db.commit()
    return db


def test_article_generation_requires_a_choice_before_api_call():
    db = _db_with_topic()
    try:
        response = generate_article(9, angle_choice="", custom_topic="", db=db)

        assert response.status_code == 303
        assert "article_error=" in response.headers["location"]
        assert db.scalar(select(func.count()).select_from(AITask)) == 0
    finally:
        db.close()


def test_custom_choice_requires_custom_topic_before_api_call():
    db = _db_with_topic()
    try:
        response = generate_article(9, angle_choice="custom", custom_topic="", db=db)

        assert response.status_code == 303
        assert "%E8%87%B3%E5%B0%91" in response.headers["location"]
        assert db.scalar(select(func.count()).select_from(AITask)) == 0
    finally:
        db.close()


def test_article_generation_searches_multiple_images_and_inserts_them(tmp_path, monkeypatch):
    db = _db_with_topic()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    monkeypatch.setattr(topic_routes, "settings", replace(topic_routes.settings, data_dir=tmp_path))

    class Provider:
        chat_config = SimpleNamespace(model="test-chat", configured=True)
        image_config = SimpleNamespace(model="test-image", configured=False)

        def generate_article(self, *_args, **_kwargs):
            return "# 标题\n\n第一段。\n\n## 背景\n\n第二段。\n\n## 影响\n\n第三段。\n\n结尾。", {"total_tokens": 1}

        def plan_image_placements(self, _title, _content, _queries):
            return [{"image_index": 1, "after_heading": "背景", "paragraph_hint": "第二段", "reason": "对应背景"}], {"total_tokens": 2}

    monkeypatch.setattr(topic_routes.AIProvider, "from_db", lambda _db: Provider())
    calls = []

    def fake_search(query, excluded, provider="360"):
        index = len(calls) + 1
        calls.append((query, set(excluded)))
        path = image_dir / f"{index}.jpg"
        path.write_bytes(b"image")
        return CommonsImage(
            file_path=path,
            source_url=f"https://commons.wikimedia.org/wiki/File:{index}.jpg",
            attribution=f"作者 {index}；CC BY",
            title=f"File:{index}.jpg",
        )

    monkeypatch.setattr(topic_routes, "search_image", fake_search)
    try:
        response = generate_article(
            9,
            angle_choice="custom",
            custom_topic="从家庭沟通角度分析这一热点",
            article_type="热点评论",
            style="清晰克制",
            target_length="2400",
            image_mode="search",
            image_instruction="",
            image_count="auto",
            db=db,
        )

        article = db.scalar(select(Article).where(Article.topic_id == 9))
        images = list(db.scalars(select(GeneratedImage).where(GeneratedImage.article_id == article.id)))
        assert response.status_code == 303
        assert len(calls) == 4
        assert len(images) == 4
        assert article.content.count("![") == 4
        assert article.content.count("/media/images/") == 4
        assert all(image.source_url in article.content for image in images)
        assert article.content.index("/media/images/1.jpg") > article.content.index("第二段。")
    finally:
        db.close()


def test_empty_model_article_is_not_saved_and_task_is_marked_failed(monkeypatch):
    db = _db_with_topic()

    class Provider:
        chat_config = SimpleNamespace(model="test-chat", configured=True)
        image_config = SimpleNamespace(model="test-image", configured=False)

        def generate_article(self, *_args, **_kwargs):
            return "   ", {"total_tokens": 1}

    monkeypatch.setattr(topic_routes.AIProvider, "from_db", lambda _db: Provider())
    try:
        response = generate_article(
            9,
            angle_choice="custom",
            custom_topic="写给普通读者的风险说明",
            target_length="1200",
            db=db,
        )

        assert response.status_code == 303
        assert "article_error=" in response.headers["location"]
        assert db.scalar(select(Article).where(Article.topic_id == 9)) is None
        task = db.scalar(select(AITask).order_by(AITask.id.desc()))
        assert task.status == "failed"
    finally:
        db.close()


def test_manual_search_uses_contextual_queries_and_preserves_image_order(tmp_path, monkeypatch):
    db = _db_with_topic()
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    monkeypatch.setattr(topic_routes, "settings", replace(topic_routes.settings, data_dir=tmp_path))

    class Provider:
        chat_config = SimpleNamespace(model="test-chat", configured=False)
        image_config = SimpleNamespace(model="test-image", configured=False)

    monkeypatch.setattr(topic_routes.AIProvider, "from_db", lambda _db: Provider())
    calls = []

    def fake_search(query, excluded, provider="360"):
        index = len(calls) + 1
        calls.append(query)
        path = image_dir / f"manual-{index}.jpg"
        path.write_bytes(b"image")
        return CommonsImage(
            file_path=path,
            source_url=f"https://image.example.com/{index}.jpg",
            attribution="来源：测试",
            title=f"manual-{index}",
        )

    monkeypatch.setattr(topic_routes, "search_image", fake_search)
    try:
        article = Article(
            topic_id=9,
            title="国产芯片热点",
            content="# 标题\n\n第一段。\n\n## 背景\n\n第二段。",
            status="draft",
        )
        db.add(article)
        db.commit()

        response = generate_image(article.id, "芯片现场", "search", "2", "after_first", db)
        db.refresh(article)

        assert response.status_code == 303
        assert len(calls) == 2
        assert calls[0] != calls[1]
        assert article.content.index("/media/images/manual-1.jpg") < article.content.index("/media/images/manual-2.jpg")
    finally:
        db.close()
