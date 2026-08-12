from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AITask, CrawlLog, CrawlRun, Source
from ..scheduler import analyze_pending_topics, run_crawl_cycle
from ..web import templates


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    tasks = list(db.scalars(select(AITask).order_by(AITask.created_at.desc()).limit(30)))
    sources = list(db.scalars(select(Source).order_by(Source.name.asc())))
    runs = list(db.scalars(select(CrawlRun).order_by(CrawlRun.created_at.desc()).limit(20)))
    active_run = db.scalar(
        select(CrawlRun).where(CrawlRun.status.in_(["pending", "running"])).order_by(CrawlRun.created_at.desc()).limit(1)
    )
    paused_run = db.scalar(
        select(CrawlRun).where(CrawlRun.status == "paused").order_by(CrawlRun.created_at.desc()).limit(1)
    )
    logs = list(db.scalars(select(CrawlLog).order_by(CrawlLog.created_at.desc()).limit(150)))
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "tasks": tasks,
            "sources": sources,
            "runs": runs,
            "active_run": active_run,
            "paused_run": paused_run,
            "logs": logs,
            "default_round_timeout_seconds": settings.crawl_round_timeout_seconds,
        },
    )


@router.post("/crawl")
def manual_crawl(
    background_tasks: BackgroundTasks,
    source_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    round_timeout_seconds: int = Form(settings.crawl_round_timeout_seconds),
):
    if not source_ids:
        return RedirectResponse("/admin?error=请至少选择一个已启用的数据源", status_code=303)
    active = db.scalar(select(CrawlRun).where(CrawlRun.status.in_(["pending", "running", "paused"])).limit(1))
    if active is not None:
        if active.status == "paused":
            return RedirectResponse(f"/admin?error=第 {active.id} 轮采集已暂停，请先点击继续或清空日志", status_code=303)
        return RedirectResponse(f"/admin?error=第 {active.id} 轮采集仍在运行，请等待完成", status_code=303)
    enabled_ids = set(db.scalars(select(Source.id).where(Source.enabled.is_(True), Source.id.in_(source_ids))))
    selected_ids = list(dict.fromkeys(source_id for source_id in source_ids if source_id in enabled_ids))
    if not selected_ids:
        return RedirectResponse("/admin?error=所选数据源均已停用或不存在", status_code=303)
    try:
        timeout_seconds = max(30, min(3600, int(round_timeout_seconds)))
    except (TypeError, ValueError):
        timeout_seconds = settings.crawl_round_timeout_seconds
    run = CrawlRun(
        trigger="manual",
        status="pending",
        total_sources=len(selected_ids),
        selected_source_ids=selected_ids,
        completed_source_ids=[],
        timeout_seconds=timeout_seconds,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(run_crawl_cycle, True, selected_ids, run.id, "manual")
    return RedirectResponse(f"/admin?message=第 {run.id} 轮采集已启动，共 {len(selected_ids)} 个数据源", status_code=303)


@router.post("/crawl/{run_id}/pause")
def pause_crawl(run_id: int, db: Session = Depends(get_db)):
    run = db.get(CrawlRun, run_id)
    if run is None:
        return RedirectResponse("/admin?error=采集轮次不存在", status_code=303)
    if run.status not in {"pending", "running"}:
        return RedirectResponse("/admin?error=当前采集轮次已经结束或已暂停", status_code=303)
    run.pause_requested = True
    db.add(CrawlLog(run_id=run.id, level="info", message="已收到暂停请求，将在当前数据源完成后暂停。"))
    db.commit()
    return RedirectResponse("/admin?message=已请求暂停，当前数据源完成后会暂停", status_code=303)


@router.post("/crawl/{run_id}/resume")
def resume_crawl(run_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    run = db.get(CrawlRun, run_id)
    if run is None:
        return RedirectResponse("/admin?error=采集轮次不存在", status_code=303)
    if run.status != "paused":
        return RedirectResponse("/admin?error=只有已暂停的采集轮次可以继续", status_code=303)
    active = db.scalar(select(CrawlRun).where(CrawlRun.status.in_(["pending", "running"])).limit(1))
    if active is not None:
        return RedirectResponse(f"/admin?error=第 {active.id} 轮采集仍在运行，请先等待完成", status_code=303)
    run.status = "pending"
    run.pause_requested = False
    run.finished_at = None
    selected_ids = list(run.selected_source_ids) if isinstance(run.selected_source_ids, list) else None
    force = run.trigger == "manual"
    db.commit()
    background_tasks.add_task(run_crawl_cycle, force, selected_ids, run.id, run.trigger)
    return RedirectResponse(f"/admin?message=第 {run.id} 轮采集已继续", status_code=303)


@router.post("/analyze")
def manual_analyze(background_tasks: BackgroundTasks):
    background_tasks.add_task(analyze_pending_topics, 10)
    return RedirectResponse("/admin?message=已按你的操作启动最多 10 条 AI 推荐", status_code=303)


@router.post("/logs/clear")
def clear_crawl_logs(db: Session = Depends(get_db)):
    completed_ids = list(db.scalars(select(CrawlRun.id).where(CrawlRun.status.notin_(["pending", "running"]))))
    if completed_ids:
        db.execute(delete(CrawlRun).where(CrawlRun.id.in_(completed_ids)))
        db.commit()
    return RedirectResponse("/admin?message=已清空历史采集日志", status_code=303)
