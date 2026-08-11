from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.main as main
import app.routes.admin as admin_routes
import app.routes.recommender as recommender_routes
import app.routes.topics as topic_routes
from app.db import get_db
from app.models import (
    AITask,
    Article,
    Base,
    CrawlLog,
    CrawlRun,
    RawItem,
    Setting,
    Source,
    Topic,
    TopicItem,
)
from app.categories import CATEGORY_OPTIONS


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def client(db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    (tmp_path / "images").mkdir()
    (tmp_path / "models").mkdir()
    monkeypatch.setattr(main, "settings", replace(main.settings, data_dir=tmp_path))
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "start_scheduler", lambda: None)
    monkeypatch.setattr(main, "stop_scheduler", lambda: None)

    application = main.create_app()

    def override_get_db():
        yield db

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as test_client:
        yield test_client


def _seed_topic(db: Session, title: str = "AI technology topic") -> Topic:
    source = Source(name="Test source", kind="rss", url="https://example.com/feed.xml")
    db.add(source)
    db.flush()
    item = RawItem(
        source_id=source.id,
        title=title,
        normalized_url="https://example.com/item-1",
        summary="A source summary",
        content="A source body",
        content_hash="hash-1",
    )
    topic = Topic(
        title=title,
        summary="A topic summary",
        category=CATEGORY_OPTIONS[0],
        recommendation_score=82,
    )
    db.add_all([item, topic])
    db.flush()
    db.add(TopicItem(topic_id=topic.id, raw_item_id=item.id))
    db.commit()
    return topic


def test_app_health_headers_and_delete_route_are_available(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    assert client.get("/articles/1/delete").status_code == 405


def test_media_mount_exposes_images_but_not_database_or_models(client: TestClient, tmp_path):
    (tmp_path / "app.db").write_text("private", encoding="utf-8")
    (tmp_path / "models" / "topic_recommender.joblib").write_bytes(b"private")
    (tmp_path / "images" / "safe.jpg").write_bytes(b"image")

    assert client.get("/media/images/safe.jpg").status_code == 200
    assert client.get("/media/app.db").status_code == 404
    assert client.get("/media/models/topic_recommender.joblib").status_code == 404


def test_article_library_save_autosave_preview_and_delete_workflow(client: TestClient, db: Session):
    topic = _seed_topic(db)
    article = Article(topic_id=topic.id, title="Original", content="# Original", status="draft")
    db.add(article)
    db.commit()
    article_id = article.id

    library = client.get("/articles?status=draft")
    assert library.status_code == 200
    assert "Original" in library.text

    blank_save = client.post(
        f"/articles/{article_id}/save",
        data={"title": " ", "content": "new body", "status": "ready"},
        follow_redirects=False,
    )
    assert blank_save.status_code == 303
    db.expire_all()
    unchanged = db.get(Article, article_id)
    assert unchanged is not None
    assert unchanged.title == "Original"

    blank_autosave = client.post(
        f"/articles/{article_id}/autosave",
        data={"title": "", "content": "", "status": "draft"},
    )
    assert blank_autosave.status_code == 422
    assert blank_autosave.json()["saved"] is False

    autosave = client.post(
        f"/articles/{article_id}/autosave",
        data={"title": "Autosaved", "content": "# Autosaved\n\nBody", "status": "ready"},
    )
    assert autosave.status_code == 200
    assert autosave.json()["saved"] is True

    saved = client.post(
        f"/articles/{article_id}/save",
        data={"title": "Saved article", "content": "# Heading\n\n**bold**", "status": "published"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    db.expire_all()
    saved_article = db.get(Article, article_id)
    assert saved_article is not None
    assert saved_article.status == "published"

    preview = client.post(
        f"/articles/{article_id}/preview",
        data={"content": "# Heading\n\n**bold**"},
    )
    assert preview.status_code == 200
    assert "<h1>Heading</h1>" in preview.text
    assert "<strong>bold</strong>" in preview.text

    deleted = client.post(f"/articles/{article_id}/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert urlsplit(deleted.headers["location"]).path == "/articles"
    db.expire_all()
    assert db.get(Article, article_id) is None
    assert db.get(Topic, topic.id) is not None


def test_source_selection_toggle_delete_and_log_clear_workflow(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    added = client.post(
        "/sources/add",
        data={"name": "Custom source", "kind": "rss", "url": "https://example.com/custom.xml"},
        follow_redirects=False,
    )
    assert added.status_code == 303
    source = db.scalar(select(Source).where(Source.name == "Custom source"))
    assert source is not None
    disabled = Source(name="Disabled source", kind="rss", url="https://example.com/disabled.xml", enabled=False)
    db.add(disabled)
    db.commit()

    called = []
    monkeypatch.setattr(admin_routes, "run_crawl_cycle", lambda *args, **kwargs: called.append((args, kwargs)))
    started = client.post(
        "/admin/crawl",
        data={"source_ids": [str(source.id), str(disabled.id)]},
        follow_redirects=False,
    )
    assert started.status_code == 303
    assert called
    run = db.scalar(select(CrawlRun).order_by(CrawlRun.id.desc()))
    assert run is not None
    assert run.total_sources == 1

    toggled = client.post(f"/sources/{source.id}/toggle", follow_redirects=False)
    assert toggled.status_code == 303
    db.expire_all()
    assert db.get(Source, source.id).enabled is False

    run.status = "success"
    db.add(CrawlLog(run_id=run.id, source_name="Custom source", message="done"))
    db.commit()
    cleared = client.post("/admin/logs/clear", follow_redirects=False)
    assert cleared.status_code == 303
    db.expire_all()
    assert db.get(CrawlRun, run.id) is None
    assert db.scalar(select(CrawlLog).where(CrawlLog.run_id == run.id)) is None

    removed = client.post(f"/sources/{source.id}/delete", follow_redirects=False)
    assert removed.status_code == 303
    assert db.get(Source, source.id) is None


@respx.mock
def test_settings_save_preserves_secret_and_model_discovery_workflow(client: TestClient, db: Session):
    initial = client.post(
        "/settings",
        data={
            "ai_chat_endpoint": "https://api.example.com/v1/chat/completions",
            "ai_chat_api_key": "secret",
            "ai_chat_model": "old-model",
            "ai_image_endpoint": "",
            "ai_image_api_key": "",
            "ai_image_model": "",
            "image_search_provider": "360",
            "preferred_keywords": "technology",
            "blocked_keywords": "",
        },
        follow_redirects=False,
    )
    assert initial.status_code == 303

    preserved = client.post(
        "/settings",
        data={
            "ai_chat_endpoint": "https://api.example.com/v1/chat/completions",
            "ai_chat_api_key": "********",
            "ai_chat_model": "",
            "ai_image_endpoint": "",
            "ai_image_api_key": "",
            "ai_image_model": "",
            "image_search_provider": "360",
            "preferred_keywords": "technology, ai",
            "blocked_keywords": "",
        },
        follow_redirects=False,
    )
    assert preserved.status_code == 303
    assert db.get(Setting, "ai_chat_api_key").value == "secret"

    models_route = respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-chat"}]})
    )
    discovered = client.post(
        "/settings/models/chat",
        data={
            "ai_chat_endpoint": "https://api.example.com/v1/chat/completions",
            "ai_chat_api_key": "********",
            "ai_chat_model": "",
        },
    )
    assert discovered.status_code == 200
    assert "deepseek-chat" in discovered.text
    assert models_route.called
    assert models_route.calls[0].request.headers["authorization"] == "Bearer secret"


def test_topic_analysis_and_article_generation_workflow(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    topic = _seed_topic(db)

    class FakeProvider:
        chat_config = SimpleNamespace(configured=True, model="fake-chat")
        image_config = SimpleNamespace(configured=False, model="")

        def analyze_topic(self, *_args, **_kwargs):
            return {
                "summary": "Verified summary",
                "conflict": "Core conflict",
                "audience": ["readers"],
                "angles": [
                    {"title": f"Angle {index}", "approach": "Approach", "reader_value": "Value"}
                    for index in range(1, 6)
                ],
                "tags": ["technology"],
                "risk_level": "low",
                "confidence": 88,
                "content_potential": 75,
            }, {"total_tokens": 10}

        def generate_article(self, *_args, **_kwargs):
            return "# Generated article\n\nBody", {"total_tokens": 20}

    monkeypatch.setattr(topic_routes.AIProvider, "from_db", lambda _db: FakeProvider())

    analyzed = client.post(f"/topics/{topic.id}/analyze", follow_redirects=False)
    assert analyzed.status_code == 303
    db.expire_all()
    analyzed_topic = db.get(Topic, topic.id)
    assert analyzed_topic.ai_summary == "Verified summary"
    assert len(analyzed_topic.ai_angles) == 5
    assert db.scalar(select(AITask).where(AITask.task_type == "analyze_topic")).status == "success"

    generated = client.post(
        f"/topics/{topic.id}/article",
        data={
            "angle_choice": "custom",
            "custom_topic": "How to understand this technology",
            "target_length": "800",
            "image_mode": "none",
        },
        follow_redirects=False,
    )
    assert generated.status_code == 303
    article = db.scalar(select(Article).where(Article.topic_id == topic.id))
    assert article is not None
    assert article.content.startswith("# Generated article")
    assert db.scalar(select(AITask).where(AITask.task_type == "generate_article")).status == "success"


def test_recommender_training_submission_and_status_workflow(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        recommender_routes,
        "gpu_status",
        lambda: {
            "cuda_ready": False,
            "available": False,
            "name": "CPU",
            "xgboost_cuda": False,
            "cuda_reason": "not available",
            "xgboost_version": None,
        },
    )

    class Queue:
        def __init__(self):
            self.tasks = []

        def add_task(self, function, *args, **kwargs):
            self.tasks.append((function, args, kwargs))

    queue = Queue()
    response = recommender_routes.train_recommender(queue, "cpu", db)
    assert response.status_code == 303
    run = db.scalar(select(CrawlRun))
    assert run is None
    training_run = db.scalar(select(recommender_routes.RecommenderTrainingRun))
    assert training_run is not None
    assert training_run.requested_device == "cpu"
    assert len(queue.tasks) == 1

    status = recommender_routes.training_status(training_run.id, db)
    payload = json.loads(status.body)
    assert payload["id"] == training_run.id
    assert payload["status"] == "pending"
    assert payload["requested_device"] == "cpu"

    invalid = recommender_routes.train_recommender(queue, "tpu", db)
    assert invalid.status_code == 303
    assert len(queue.tasks) == 1
