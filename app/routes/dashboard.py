from __future__ import annotations

from math import ceil

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..categories import CATEGORY_OPTIONS, categorize_topic
from ..models import Topic
from ..repositories import get_setting
from ..web import templates


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = Query(default=""),
    category: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    page_size = 30
    active_category = category if category in CATEGORY_OPTIONS else ""
    filters = [Topic.item_links.any()]
    if q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(or_(Topic.title.ilike(pattern), Topic.summary.ilike(pattern)))
    if active_category:
        filters.append(Topic.category == active_category)

    total_count = int(db.scalar(select(func.count(Topic.id)).where(*filters)) or 0)
    total_pages = max(1, ceil(total_count / page_size))
    current_page = min(page, total_pages)
    statement = (
        select(Topic)
        .where(*filters)
        .order_by(Topic.recommendation_score.desc(), Topic.last_seen_at.desc())
        .offset((current_page - 1) * page_size)
        .limit(page_size)
    )
    topics = list(db.scalars(statement))
    for topic in topics:
        topic.display_category = topic.category or categorize_topic(topic.title, topic.summary, topic.ai_tags)
    count_filters = [Topic.item_links.any()]
    if q.strip():
        count_filters.append(or_(Topic.title.ilike(pattern), Topic.summary.ilike(pattern)))
    category_counts = {name: 0 for name in CATEGORY_OPTIONS}
    for name, count in db.execute(
        select(Topic.category, func.count(Topic.id)).where(*count_filters).group_by(Topic.category)
    ):
        if name in category_counts:
            category_counts[name] = int(count)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "topics": topics,
            "q": q,
            "active_category": active_category,
            "category_counts": category_counts,
            "category_options": CATEGORY_OPTIONS,
            "page": current_page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "preferred_keywords": get_setting(db, "preferred_keywords"),
        },
    )
