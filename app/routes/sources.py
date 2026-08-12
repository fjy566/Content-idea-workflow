from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from urllib.parse import quote_plus
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Source
from ..repositories import delete_source, list_sources, save_source
from ..security import validate_public_url
from ..source_catalog import CHINA_SOURCE_PRESETS, seed_china_sources
from ..web import templates


router = APIRouter(prefix="/sources", tags=["sources"])


def _bounded_source_timeout(value: object) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        seconds = settings.source_timeout_seconds
    return max(5, min(300, seconds))


@router.get("", response_class=HTMLResponse)
def sources_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context={"sources": list_sources(db), "china_source_presets": CHINA_SOURCE_PRESETS},
    )


@router.post("/seed-china")
def restore_china_sources(db: Session = Depends(get_db)):
    added = seed_china_sources(db)
    return RedirectResponse(f"/sources?notice={quote_plus(f'已补充 {added} 个中国精选数据源')}", status_code=303)


@router.post("/add")
def add_source(
    name: str = Form(...),
    kind: str = Form(...),
    url: str = Form(...),
    interval_minutes: int = Form(30),
    timeout_seconds: int = Form(settings.source_timeout_seconds),
    item_selector: str = Form(""),
    title_selector: str = Form(""),
    link_selector: str = Form(""),
    summary_selector: str = Form(""),
    published_selector: str = Form(""),
    db: Session = Depends(get_db),
):
    if kind not in {"rss", "html", "html_js"}:
        return RedirectResponse("/sources?error=不支持的数据源类型", status_code=303)
    normalized_url = url.strip()[:2000]
    if not name.strip() or not normalized_url:
        return RedirectResponse("/sources?error=名称和地址不能为空", status_code=303)
    try:
        validate_public_url(normalized_url)
    except ValueError as exc:
        return RedirectResponse(f"/sources?error={quote_plus(str(exc))}", status_code=303)
    source = Source(
        name=name.strip()[:200],
        kind=kind,
        url=normalized_url,
        interval_minutes=max(5, min(1440, interval_minutes)),
        timeout_seconds=_bounded_source_timeout(timeout_seconds),
        item_selector=item_selector.strip()[:500] or None,
        title_selector=title_selector.strip()[:500] or None,
        link_selector=link_selector.strip()[:500] or None,
        summary_selector=summary_selector.strip()[:500] or None,
        published_selector=published_selector.strip()[:500] or None,
    )
    save_source(db, source)
    return RedirectResponse("/sources", status_code=303)


@router.post("/{source_id}/toggle")
def toggle_source(source_id: int, db: Session = Depends(get_db)):
    source = db.get(Source, source_id)
    if source is not None:
        source.enabled = not source.enabled
        db.commit()
    return RedirectResponse("/sources", status_code=303)


@router.post("/{source_id}/timeout")
def update_source_timeout(
    source_id: int,
    timeout_seconds: int = Form(...),
    db: Session = Depends(get_db),
):
    source = db.get(Source, source_id)
    if source is None:
        return RedirectResponse("/sources?error=数据源不存在", status_code=303)
    source.timeout_seconds = _bounded_source_timeout(timeout_seconds)
    db.commit()
    return RedirectResponse("/sources?notice=已保存该数据源的超时时间", status_code=303)


@router.post("/{source_id}/delete")
def remove_source(source_id: int, db: Session = Depends(get_db)):
    delete_source(db, source_id)
    return RedirectResponse("/sources", status_code=303)
