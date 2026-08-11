from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..db import get_db
from ..models import Article, Topic, utcnow
from ..web import templates


router = APIRouter(prefix="/articles", tags=["articles"])
STATUS_LABELS = {"draft": "草稿", "ready": "待发布", "published": "已发布"}


@router.get("", response_class=HTMLResponse)
def article_library(
    request: Request,
    status: str = Query(default=""),
    q: str = Query(default=""),
    db: Session = Depends(get_db),
):
    active_status = status if status in STATUS_LABELS else ""
    statement = (
        select(Article)
        .options(selectinload(Article.topic), selectinload(Article.images))
        .order_by(Article.updated_at.desc(), Article.created_at.desc())
        .limit(200)
    )

    if active_status:
        statement = statement.where(Article.status == active_status)
    if q.strip():
        pattern = f"%{q.strip()}%"
        statement = statement.join(Topic).where(
            or_(Article.title.ilike(pattern), Article.content.ilike(pattern), Topic.title.ilike(pattern))
        )
    articles = list(db.scalars(statement))
    counts = {key: 0 for key in STATUS_LABELS}
    for value, count in db.execute(select(Article.status, func.count(Article.id)).group_by(Article.status)):
        if value in counts:
            counts[value] = int(count)
    return templates.TemplateResponse(
        request=request,
        name="articles.html",
        context={
            "articles": articles,
            "active_status": active_status,
            "q": q,
            "status_labels": STATUS_LABELS,
            "status_counts": counts,
            "total_count": sum(counts.values()),
        },
    )


@router.post("/{article_id}/autosave", response_class=JSONResponse)
def autosave_article(
    article_id: int,
    title: str = Form(...),
    content: str = Form(...),
    status: str = Form("draft"),
    db: Session = Depends(get_db),
):
    """Persist an in-progress edit without navigating away from the editor."""
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="Article not found")
    article.title = title.strip()[:1000]
    article.content = content[:200_000]
    article.status = status if status in STATUS_LABELS else "draft"
    article.updated_at = utcnow()
    db.commit()
    return {"saved": True, "updated_at": article.updated_at.isoformat(), "status": article.status}
