from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai_provider import AIProvider, AIProviderError
from ..article_format import (
    article_image_queries,
    image_markdown,
    image_is_inserted,
    insert_image_markdown,
    insert_images_by_plan,
    insert_images_evenly,
    render_markdown,
    resolve_image_count,
)
from ..categories import categorize_topic
from ..config import settings
from ..db import get_db
from ..image_search import DEFAULT_IMAGE_SEARCH_PROVIDER, image_search_label, search_image
from ..models import AITask, Article, GeneratedImage, RawItem, Source, Topic, TopicItem, utcnow
from ..recommender import record_feedback
from ..repositories import get_setting
from ..topic_pipeline import calculate_baseline_score
from ..topic_recommendations import normalize_recommendations, resolve_writing_angle
from ..topic_status import is_topic_handled
from ..utils import clean_text, relative_file_path
from ..web import templates


router = APIRouter(tags=["topics"])

ARTICLE_TYPES = ("热点评论", "科普解读", "避坑指南", "清单教程", "事件复盘")
ARTICLE_STYLES = ("清晰克制", "通俗有冲突", "理性专业", "轻松口语")
ARTICLE_LENGTH_PRESETS = (800, 1200, 1800, 3000)


def _get_topic(db: Session, topic_id: int) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


def _sources_for(db: Session, topic_id: int):
    rows = db.execute(
        select(RawItem, Source)
        .join(TopicItem, TopicItem.raw_item_id == RawItem.id)
        .join(Source, Source.id == RawItem.source_id)
        .where(TopicItem.topic_id == topic_id)
        .order_by(RawItem.published_at.desc().nullslast(), RawItem.fetched_at.desc())
    ).all()
    return list(rows)


def _new_task(db: Session, task_type: str, model: str | None) -> AITask:
    task = AITask(task_type=task_type, status="running", model=model)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _finish_task(db: Session, task: AITask, status: str, error: str | None = None, usage: dict | None = None) -> None:
    task.status = status
    task.error_message = error[:4000] if error else None
    task.usage_json = usage or {}
    task.finished_at = utcnow()
    db.commit()


def _list_values(value: object, limit: int = 10) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(item, 200) for item in value if clean_text(item, 200)][:limit]


def _target_length(value: object) -> int:
    try:
        length = int(str(value))
    except (TypeError, ValueError):
        raise ValueError("目标篇幅必须是 300 到 10000 之间的整数")
    if not 300 <= length <= 10_000:
        raise ValueError("目标篇幅必须在 300 到 10000 字之间")
    return length


@router.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_page(request: Request, topic_id: int, db: Session = Depends(get_db)):
    topic = _get_topic(db, topic_id)
    topic.display_category = categorize_topic(topic.title, topic.summary, topic.ai_tags)
    provider = AIProvider.from_db(db)
    try:
        record_feedback(db, topic_id, "view")
    except ValueError:
        pass
    topic_is_handled = is_topic_handled(topic)
    return templates.TemplateResponse(
        request=request,
        name="topic.html",
        context={
            "topic": topic,
            "topic_is_handled": topic_is_handled,
            "source_rows": _sources_for(db, topic_id),
            "chat_configured": provider.chat_config.configured,
            "chat_model": provider.chat_config.model,
            "image_configured": provider.image_config.configured,
            "image_search_provider": get_setting(db, "image_search_provider", DEFAULT_IMAGE_SEARCH_PROVIDER),
            "image_search_provider_label": image_search_label(get_setting(db, "image_search_provider", DEFAULT_IMAGE_SEARCH_PROVIDER)),
            "notice": request.query_params.get("notice", ""),
            "article_error": request.query_params.get("article_error", ""),
            "recommendations": normalize_recommendations(topic.ai_angles),
            "article_types": ARTICLE_TYPES,
            "article_styles": ARTICLE_STYLES,
            "article_length_presets": ARTICLE_LENGTH_PRESETS,
            "topic_articles": sorted(topic.articles, key=lambda article: article.updated_at, reverse=True)[:5],
        },
    )


@router.post("/topics/{topic_id}/analyze")
def analyze_topic(topic_id: int, db: Session = Depends(get_db)):
    topic = _get_topic(db, topic_id)
    provider = AIProvider.from_db(db)
    if not provider.chat_config.configured:
        return RedirectResponse(f"/topics/{topic_id}?notice=api_missing", status_code=303)
    task = _new_task(db, "analyze_topic", provider.chat_config.model or None)
    try:
        result, usage = provider.analyze_topic(topic, _sources_for(db, topic_id), get_setting(db, "preferred_keywords"))
        topic.ai_summary = clean_text(result.get("summary"), 5000)
        topic.ai_conflict = clean_text(result.get("conflict"), 2000)
        topic.ai_audience = _list_values(result.get("audience"))
        topic.ai_angles = normalize_recommendations(result.get("angles"), required_count=5)
        topic.ai_tags = _list_values(result.get("tags"), 20)
        topic.ai_risk_level = clean_text(result.get("risk_level"), 30).lower() or "medium"
        topic.ai_confidence = float(result.get("confidence", 0) or 0)
        potential = float(result.get("content_potential", 0) or 0)
        topic.content_quality = max(topic.content_quality, min(100.0, potential))
        topic.risk_score = {"low": 15.0, "medium": 45.0, "high": 85.0}.get(topic.ai_risk_level, topic.risk_score)
        topic.ai_error = None
        topic.analyzed_at = utcnow()
        calculate_baseline_score(db, topic)
        db.commit()
        _finish_task(db, task, "success", usage=usage)
    except (AIProviderError, ValueError, TypeError) as exc:
        topic.ai_error = str(exc)
        db.commit()
        _finish_task(db, task, "failed", error=str(exc))
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@router.post("/topics/{topic_id}/article")
def generate_article(
    topic_id: int,
    angle_choice: str = Form(""),
    custom_topic: str = Form(""),
    article_type: str = Form("热点评论"),
    style: str = Form("清晰克制"),
    target_length: str = Form("1200"),
    image_mode: str = Form("none"),
    image_instruction: str = Form(""),
    image_count: str = Form("auto"),
    image_insert_mode: str = Form("smart"),
    db: Session = Depends(get_db),
):
    topic = _get_topic(db, topic_id)
    recommendations = normalize_recommendations(topic.ai_angles)
    try:
        selected_angle = resolve_writing_angle(recommendations, angle_choice, custom_topic)
    except ValueError as exc:
        return RedirectResponse(f"/topics/{topic_id}?article_error={quote_plus(str(exc))}#article-builder", status_code=303)
    article_type = article_type if article_type in ARTICLE_TYPES else "热点评论"
    style = style if style in ARTICLE_STYLES else "清晰克制"
    try:
        target_length_value = _target_length(target_length)
    except ValueError as exc:
        return RedirectResponse(f"/topics/{topic_id}?article_error={quote_plus(str(exc))}#article-builder", status_code=303)
    image_mode = image_mode if isinstance(image_mode, str) and image_mode in {"none", "search", "ai"} else "none"
    image_insert_mode = image_insert_mode if image_insert_mode in {"smart", "even", "library"} else "smart"
    image_instruction = clean_text(image_instruction if isinstance(image_instruction, str) else "", 500)
    provider = AIProvider.from_db(db)
    task = _new_task(db, "generate_article", provider.chat_config.model or None)
    try:
        content, usage = provider.generate_article(
            topic,
            article_type,
            style,
            _sources_for(db, topic_id),
            selected_angle=selected_angle,
            target_length=str(target_length_value),
        )
        content = str(content or "").strip()[:100_000]
        if not content:
            raise AIProviderError("文本模型返回了空文章正文")
        title = topic.title
        for line in content.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                title = stripped[:1000]
                break
        article = Article(topic_id=topic.id, title=title, content=content, model=provider.chat_config.model)
        db.add(article)
        db.commit()
        db.refresh(article)
        record_feedback(db, topic_id, "generate", article.id)
        record_feedback(db, topic_id, "choose_angle", article.id)
        _finish_task(db, task, "success", usage=usage)
        image_notice = ""
        if image_mode != "none":
            requested_count = resolve_image_count(image_count if isinstance(image_count, str) else "auto", target_length_value)
            image_task = _new_task(
                db,
                "search_image" if image_mode == "search" else "generate_image",
                provider.image_config.model if image_mode == "ai" else None,
            )
            created_images: list[GeneratedImage] = []
            image_errors: list[str] = []
            excluded_urls: set[str] = set()
            queries = article_image_queries(article.title, article.content, image_instruction, requested_count)
            for index, query in enumerate(queries, start=1):
                try:
                    if image_mode == "search":
                        found = search_image(
                            query,
                            excluded_urls,
                            get_setting(db, "image_search_provider", DEFAULT_IMAGE_SEARCH_PROVIDER),
                        )
                        excluded_urls.add(found.source_url)
                        image = GeneratedImage(
                            article_id=article.id,
                            file_path=relative_file_path(found.file_path, settings.data_dir),
                            prompt=query,
                            provider=found.provider,
                            source_url=found.source_url,
                            attribution=found.attribution,
                        )
                    else:
                        prompt = image_instruction or f"为中文文章《{article.title}》的“{query}”部分创作第 {index} 张配图，新闻评论视觉风格，不添加文字"
                        path, provider_name = provider.save_image(prompt)
                        image = GeneratedImage(
                            article_id=article.id,
                            file_path=relative_file_path(Path(path), settings.data_dir),
                            prompt=prompt,
                            provider=provider_name,
                        )
                    db.add(image)
                    db.flush()
                    created_images.append(image)
                except (AIProviderError, OSError, ValueError) as exc:
                    image_errors.append(f"第 {index} 张：{exc}")
                    if image_mode == "ai" and not provider.image_config.configured:
                        break
            placement_usage: dict = {}
            placement_count = 0
            placement_fallback = False
            if created_images and image_insert_mode != "library":
                if image_insert_mode == "smart" and provider.chat_config.configured:
                    planner = getattr(provider, "plan_image_placements", None)
                    if callable(planner):
                        try:
                            placements, placement_usage = planner(article.title, article.content, queries)
                            article.content = insert_images_by_plan(article.content, created_images, placements)
                            placement_count = len(placements)
                        except (AIProviderError, ValueError, TypeError) as exc:
                            placement_fallback = True
                            image_errors.append(f"智能排版未完成，已回退均匀排版：{exc}")
                            article.content = insert_images_evenly(article.content, created_images)
                    else:
                        placement_fallback = True
                        article.content = insert_images_evenly(article.content, created_images)
                else:
                    article.content = insert_images_evenly(article.content, created_images)
                db.commit()
                record_feedback(db, topic_id, "add_image", article.id)
            image_usage = {
                "requested_count": requested_count,
                "created_count": len(created_images),
                "insert_mode": image_insert_mode,
                "placement_count": placement_count,
                "placement_fallback": placement_fallback,
                "placement_usage": placement_usage,
            }
            task_status = "success" if len(created_images) == requested_count else ("partial" if created_images else "failed")
            error_text = "；".join(image_errors) if image_errors else None
            _finish_task(db, image_task, task_status, error=error_text, usage=image_usage)
            if image_insert_mode == "library":
                image_notice = f"文章已生成，{len(created_images)}/{requested_count} 张配图已保存到素材库"
            elif placement_fallback:
                image_notice = f"文章已生成，{len(created_images)}/{requested_count} 张配图已按章节均匀插入（智能排版失败已自动回退）"
            elif image_insert_mode == "smart" and placement_count:
                image_notice = f"文章已生成，{len(created_images)}/{requested_count} 张配图已由文本模型按章节插入正文"
            else:
                image_notice = f"文章已生成，{len(created_images)}/{requested_count} 张配图已按篇幅均匀插入正文"
            if image_errors:
                image_notice += f"；{len(image_errors)} 张处理失败，可在素材库重试"
        destination = f"/articles/{article.id}"
        if image_notice:
            destination += f"?notice={quote_plus(image_notice)}"
        return RedirectResponse(destination, status_code=303)
    except (AIProviderError, ValueError, TypeError) as exc:
        _finish_task(db, task, "failed", error=str(exc))
        return RedirectResponse(f"/topics/{topic_id}?article_error={quote_plus(str(exc))}#article-builder", status_code=303)


@router.post("/topics/{topic_id}/feedback")
def topic_feedback(topic_id: int, action: str = Form(...), db: Session = Depends(get_db)):
    _get_topic(db, topic_id)
    try:
        record_feedback(db, topic_id, action)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported feedback action")
    return RedirectResponse(f"/topics/{topic_id}", status_code=303)


@router.get("/articles/{article_id}", response_class=HTMLResponse)
def article_page(request: Request, article_id: int, db: Session = Depends(get_db)):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    provider = AIProvider.from_db(db)
    return templates.TemplateResponse(
        request=request,
        name="article.html",
        context={
            "article": article,
            "notice": request.query_params.get("notice", ""),
            "image_configured": provider.image_config.configured,
            "image_search_provider": get_setting(db, "image_search_provider", DEFAULT_IMAGE_SEARCH_PROVIDER),
            "image_search_provider_label": image_search_label(get_setting(db, "image_search_provider", DEFAULT_IMAGE_SEARCH_PROVIDER)),
            "preview_html": render_markdown(article.content),
            "image_inserted": {image.id: image_is_inserted(article.content, image) for image in article.images},
            "image_payload": {str(image.id): image_markdown(image) for image in article.images},
        },
    )


@router.post("/articles/{article_id}/save")
def save_article(
    article_id: int,
    title: str = Form(...),
    content: str = Form(...),
    status: str = Form("draft"),
    db: Session = Depends(get_db),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    normalized_title = title.strip()[:1000]
    normalized_content = content[:200_000]
    if not normalized_title:
        return RedirectResponse(
            f"/articles/{article_id}?notice={quote_plus('文章标题不能为空')}",
            status_code=303,
        )
    if not normalized_content.strip():
        return RedirectResponse(
            f"/articles/{article_id}?notice={quote_plus('文章正文不能为空')}",
            status_code=303,
        )
    article.title = normalized_title
    article.content = normalized_content
    previous_status = article.status
    article.status = status if status in {"draft", "ready", "published"} else "draft"
    db.commit()
    action = "publish" if article.status == "published" and previous_status != "published" else "edit"
    record_feedback(db, article.topic_id, action, article.id)
    label = {"draft": "草稿", "ready": "待发布", "published": "已发布"}[article.status]
    return RedirectResponse(f"/articles/{article_id}?notice={quote_plus(f'文章已保存为“{label}”，可在文章库中找到')}", status_code=303)


@router.post("/articles/{article_id}/image")
def generate_image(
    article_id: int,
    prompt: str = Form(...),
    mode: str = Form("ai"),
    quantity: str = Form("1"),
    insert_position: str = Form("library"),
    db: Session = Depends(get_db),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    if mode not in {"ai", "search"}:
        raise HTTPException(status_code=400, detail="Unsupported image mode")
    provider = AIProvider.from_db(db)
    task = _new_task(db, "generate_image" if mode == "ai" else "search_image", provider.image_config.model if mode == "ai" else None)
    prompt = clean_text(prompt, 5000)
    try:
        requested_count = max(1, min(6, int(quantity)))
    except (TypeError, ValueError):
        requested_count = 1
    insert_position = insert_position if insert_position in {"library", "auto", "start", "after_first", "middle", "end"} else "library"
    if not prompt:
        _finish_task(db, task, "failed", error="请填写图片说明或搜索词")
        return RedirectResponse(
            f"/articles/{article_id}?notice={quote_plus('请填写图片说明或搜索词')}",
            status_code=303,
        )
    created_images: list[GeneratedImage] = []
    errors: list[str] = []
    excluded_urls = {image.source_url for image in article.images if image.source_url}
    queries = article_image_queries(article.title, article.content, prompt, requested_count)
    for index in range(1, requested_count + 1):
        try:
            if mode == "search":
                search_query = queries[index - 1]
                found = search_image(
                    search_query,
                    excluded_urls,
                    get_setting(db, "image_search_provider", DEFAULT_IMAGE_SEARCH_PROVIDER),
                )
                excluded_urls.add(found.source_url)
                image = GeneratedImage(
                    article_id=article.id,
                    file_path=relative_file_path(found.file_path, settings.data_dir),
                    prompt=search_query,
                    provider=found.provider,
                    source_url=found.source_url,
                    attribution=found.attribution,
                )
            else:
                varied_prompt = f"{prompt}。这是同一文章的第 {index} 张配图，构图应与其他图片有区别。" if requested_count > 1 else prompt
                path, provider_name = provider.save_image(varied_prompt)
                image = GeneratedImage(
                    article_id=article.id,
                    file_path=relative_file_path(Path(path), settings.data_dir),
                    prompt=varied_prompt,
                    provider=provider_name,
                )
            db.add(image)
            db.flush()
            created_images.append(image)
        except (AIProviderError, OSError, ValueError) as exc:
            errors.append(f"第 {index} 张：{exc}")
            if mode == "ai" and not provider.image_config.configured:
                break
    if created_images:
        if insert_position == "auto":
            article.content = insert_images_evenly(article.content, created_images)
        elif insert_position != "library":
            ordered_images = (
                list(reversed(created_images))
                if insert_position in {"start", "after_first", "middle"}
                else created_images
            )
            for image in ordered_images:
                article.content = insert_image_markdown(article.content, image, insert_position)
        db.commit()
        record_feedback(db, article.topic_id, "add_image", article.id)
    status = "success" if len(created_images) == requested_count else ("partial" if created_images else "failed")
    _finish_task(db, task, status, error="；".join(errors) if errors else None)
    notice = f"已获得 {len(created_images)}/{requested_count} 张配图"
    if insert_position != "library" and created_images:
        notice += "，并插入正文"
    if errors:
        notice += f"；{len(errors)} 张失败"
    return RedirectResponse(f"/articles/{article_id}?notice={quote_plus(notice)}", status_code=303)


@router.post("/articles/{article_id}/images/{image_id}/insert")
def insert_existing_image(
    article_id: int,
    image_id: int,
    position: str = Form("end"),
    db: Session = Depends(get_db),
):
    article = db.get(Article, article_id)
    image = db.get(GeneratedImage, image_id)
    if article is None or image is None or image.article_id != article_id:
        raise HTTPException(status_code=404, detail="Article image not found")
    if image_is_inserted(article.content, image):
        notice = "这张图片已经在正文中"
    else:
        article.content = insert_image_markdown(article.content, image, position)
        db.commit()
        notice = "图片已插入正文"
    return RedirectResponse(f"/articles/{article_id}?notice={quote_plus(notice)}#image-{image_id}", status_code=303)


@router.post("/articles/{article_id}/preview", response_class=HTMLResponse)
def preview_article(article_id: int, content: str = Form(""), db: Session = Depends(get_db)):
    if db.get(Article, article_id) is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return HTMLResponse(str(render_markdown(content)))
