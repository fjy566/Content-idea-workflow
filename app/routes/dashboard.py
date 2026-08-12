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
from ..topic_status import handled_topic_expression, unhandled_topic_expression
from ..web import templates


router = APIRouter()
TOPIC_SCOPE_LABELS = {"pending": "待处理", "handled": "已处理", "all": "全部"}


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = Query(default=""),
    category: str = Query(default=""),
    scope: str = Query(default="pending"),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    page_size = 30
    active_category = category if category in CATEGORY_OPTIONS else ""
    active_scope = scope if scope in TOPIC_SCOPE_LABELS else "pending"
    base_filters = [Topic.item_links.any()]
    if q.strip():
        pattern = f"%{q.strip()}%"
        base_filters.append(or_(Topic.title.ilike(pattern), Topic.summary.ilike(pattern)))

    handled_expression = handled_topic_expression()
    if active_scope == "pending":
        scope_expression = unhandled_topic_expression()
    elif active_scope == "handled":
        scope_expression = handled_expression
    else:
        scope_expression = None

    filters = list(base_filters)
    if active_category:
        filters.append(Topic.category == active_category)
    if scope_expression is not None:
        filters.append(scope_expression)

    total_count = int(db.scalar(select(func.count(Topic.id)).where(*filters)) or 0)
    total_pages = max(1, ceil(total_count / page_size))
    current_page = min(page, total_pages)
    statement = select(Topic, handled_expression.label("is_handled")).where(*filters)
    rows = db.execute(
        statement
        .order_by(Topic.recommendation_score.desc(), Topic.last_seen_at.desc())
        .offset((current_page - 1) * page_size)
        .limit(page_size)
    ).all()
    topics = []
    for topic, is_handled in rows:
        topic.is_handled = bool(is_handled)
        topic.display_category = topic.category or categorize_topic(topic.title, topic.summary, topic.ai_tags)
        topics.append(topic)

    category_filters = list(base_filters)
    if scope_expression is not None:
        category_filters.append(scope_expression)
    category_counts = {name: 0 for name in CATEGORY_OPTIONS}
    for name, count in db.execute(
        select(Topic.category, func.count(Topic.id)).where(*category_filters).group_by(Topic.category)
    ):
        if name in category_counts:
            category_counts[name] = int(count)

    scope_counts: dict[str, int] = {}
    for scope_name, expression in (
        ("pending", unhandled_topic_expression()),
        ("handled", handled_expression),
        ("all", None),
    ):
        count_filters = list(base_filters)
        if expression is not None:
            count_filters.append(expression)
        scope_counts[scope_name] = int(db.scalar(select(func.count(Topic.id)).where(*count_filters)) or 0)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "topics": topics,
            "q": q,
            "active_category": active_category,
            "scope": active_scope,
            "scope_labels": TOPIC_SCOPE_LABELS,
            "scope_counts": scope_counts,
            "category_counts": category_counts,
            "category_options": CATEGORY_OPTIONS,
            "page": current_page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "preferred_keywords": get_setting(db, "preferred_keywords"),
        },
    )
