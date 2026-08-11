from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.config import settings
from app.models import Article, Base, GeneratedImage, Topic
from app.routes.articles import article_library, autosave_article, delete_article_from_library
from app.routes.topics import save_article


def _request(path: str = "/articles") -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": path,
        "root_path": "",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("test", 123),
        "server": ("test", 80),
    })


def test_article_library_lists_saved_articles_and_filters_status():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        topic = Topic(title="真实热点", summary="来源摘要")
        db.add(topic)
        db.flush()
        db.add_all([
            Article(topic_id=topic.id, title="草稿文章", content="正文", status="draft"),
            Article(topic_id=topic.id, title="待发布文章", content="正文", status="ready"),
        ])
        db.commit()

        all_page = article_library(_request(), status="", q="", db=db)
        ready_page = article_library(_request(), status="ready", q="", db=db)
        all_html = all_page.body.decode("utf-8")
        ready_html = ready_page.body.decode("utf-8")

        assert "草稿文章" in all_html and "待发布文章" in all_html
        assert "待发布文章" in ready_html
        assert "草稿文章" not in ready_html
        assert "草稿" in all_html and "待发布" in all_html


def test_save_article_updates_status_and_returns_discoverable_notice():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        topic = Topic(title="热点", summary="摘要")
        db.add(topic)
        db.flush()
        article = Article(topic_id=topic.id, title="原题", content="原文", status="draft")
        db.add(article)
        db.commit()

        response = save_article(article.id, "新标题", "新正文", "ready", db)

        db.refresh(article)
        assert article.status == "ready"
        assert article.title == "新标题"
        assert "notice=" in response.headers["location"]
        assert db.scalar(select(Article).where(Article.status == "ready")).id == article.id


def test_autosave_persists_without_redirecting_from_editor():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        topic = Topic(title="热点", summary="摘要")
        db.add(topic)
        db.flush()
        article = Article(topic_id=topic.id, title="原题", content="原文", status="draft")
        db.add(article)
        db.commit()

        response = autosave_article(article.id, "自动保存题目", "自动保存正文", "ready", db)

        db.refresh(article)
        assert response["saved"] is True
        assert response["status"] == "ready"
        assert article.title == "自动保存题目"
        assert article.content == "自动保存正文"
        assert article.status == "ready"


def test_save_article_rejects_blank_title_or_content_without_overwriting_article():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        topic = Topic(title="热点", summary="摘要")
        article = Article(topic=topic, title="原标题", content="原正文", status="draft")
        db.add(article)
        db.commit()

        blank_title = save_article(article.id, "   ", "新正文", "draft", db)
        blank_content = save_article(article.id, "新标题", " \n ", "draft", db)

        db.refresh(article)
        assert blank_title.status_code == 303
        assert "文章标题不能为空" in unquote(blank_title.headers["location"])
        assert blank_content.status_code == 303
        assert "文章正文不能为空" in unquote(blank_content.headers["location"])
        assert article.title == "原标题"
        assert article.content == "原正文"


def test_delete_article_removes_article_and_local_images_but_keeps_topic(monkeypatch, tmp_path: Path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    image_path = tmp_path / "images" / "article.jpg"
    image_path.parent.mkdir()
    image_path.write_bytes(b"article-image")

    with Session(engine) as db:
        topic = Topic(title="保留的来源热点", summary="热点仍然存在")
        article = Article(topic=topic, title="待删除文章", content="正文", status="draft")
        article.images.append(GeneratedImage(file_path="images/article.jpg", prompt="配图"))
        db.add(article)
        db.commit()
        article_id = article.id

        monkeypatch.setattr("app.routes.articles.settings", replace(settings, data_dir=tmp_path))
        response = delete_article_from_library(article_id, "draft", "来源", db)

        assert response.status_code == 303
        assert "notice=" in response.headers["location"]
        assert db.get(Article, article_id) is None
        assert db.scalar(select(Topic).where(Topic.id == topic.id)) is not None
        assert db.scalar(select(GeneratedImage).where(GeneratedImage.article_id == article_id)) is None
        assert not image_path.exists()


def test_article_library_exposes_explicit_post_delete_action():
    source = Path("app/templates/articles.html").read_text(encoding="utf-8")
    assert 'method="post" action="/articles/{{ article.id }}/delete"' in source
    assert "删除文章" in source
    assert "来源热点会保留" in source
    assert "此操作不可恢复" in source


def test_delete_article_does_not_remove_a_shared_image_until_last_reference_is_deleted(monkeypatch, tmp_path: Path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    image_path = tmp_path / "images" / "shared.jpg"
    image_path.parent.mkdir()
    image_path.write_bytes(b"shared-image")

    with Session(engine) as db:
        topic = Topic(title="共享素材热点", summary="摘要")
        first = Article(topic=topic, title="第一篇", content="正文")
        second = Article(topic=topic, title="第二篇", content="正文")
        first.images.append(GeneratedImage(file_path="images/shared.jpg", prompt="共享图"))
        second.images.append(GeneratedImage(file_path="images/shared.jpg", prompt="共享图"))
        db.add_all([first, second])
        db.commit()
        first_id, second_id = first.id, second.id

        monkeypatch.setattr("app.routes.articles.settings", replace(settings, data_dir=tmp_path))
        delete_article_from_library(first_id, "", "", db)
        assert image_path.exists()

        delete_article_from_library(second_id, "", "", db)
        assert not image_path.exists()


def test_delete_article_never_removes_image_outside_data_directory(monkeypatch, tmp_path: Path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    data_dir = tmp_path / "data"
    outside_path = tmp_path / "outside.jpg"
    outside_path.write_bytes(b"keep")

    with Session(engine) as db:
        topic = Topic(title="路径安全热点", summary="摘要")
        article = Article(topic=topic, title="待删除", content="正文", status="draft")
        article.images.append(GeneratedImage(file_path="../outside.jpg", prompt="路径测试"))
        db.add(article)
        db.commit()

        monkeypatch.setattr("app.routes.articles.settings", replace(settings, data_dir=data_dir))
        delete_article_from_library(article.id, "", "", db)

    assert outside_path.exists()
