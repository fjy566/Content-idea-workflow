from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..models import RecommenderTrainingRun, utcnow
from ..recommender import gpu_status, recommender_status, train_model
from ..web import templates


router = APIRouter(prefix="/recommender", tags=["recommender"])


@router.get("", response_class=HTMLResponse)
def recommender_page(request: Request, message: str = "", run_id: int | None = None, db: Session = Depends(get_db)):
    training_run = db.get(RecommenderTrainingRun, run_id) if run_id else None
    return templates.TemplateResponse(
        request=request,
        name="recommender.html",
        context={"status": recommender_status(db), "message": message, "training_run": training_run},
    )


def _training_payload(run: RecommenderTrainingRun) -> dict[str, object]:
    def iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()

    return {
        "id": run.id,
        "requested_device": run.requested_device,
        "actual_device": run.actual_device,
        "status": run.status,
        "phase": run.phase,
        "progress": round(float(run.progress or 0.0), 1),
        "message": run.message,
        "sample_count": run.sample_count,
        "metrics": run.metrics_json or {},
        "error": run.error_message,
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
    }


def _run_training_job(run_id: int, requested_device: str) -> None:
    db = SessionLocal()

    def update(update_values: dict[str, object]) -> None:
        run = db.get(RecommenderTrainingRun, run_id)
        if run is None:
            return
        run.phase = str(update_values.get("phase") or run.phase)
        run.progress = float(update_values.get("progress") or run.progress)
        run.message = str(update_values.get("message") or run.message)
        if update_values.get("device"):
            run.actual_device = str(update_values["device"])
        if update_values.get("samples") is not None:
            run.sample_count = int(update_values["samples"])
        if isinstance(update_values.get("metrics"), dict):
            run.metrics_json = update_values["metrics"]
        db.commit()

    try:
        run = db.get(RecommenderTrainingRun, run_id)
        if run is None:
            return
        run.status = "running"
        run.phase = "starting"
        run.progress = 2
        run.message = "训练任务已启动。"
        run.started_at = utcnow()
        db.commit()
        metrics = train_model(db, requested_device=requested_device, progress_callback=update)
        run = db.get(RecommenderTrainingRun, run_id)
        if run is not None:
            run.status = "succeeded"
            run.phase = "completed"
            run.progress = 100
            run.message = "训练完成，热点排序已更新。"
            run.actual_device = str(metrics.get("device") or run.actual_device or "CPU")
            run.sample_count = int(metrics.get("samples") or run.sample_count)
            run.metrics_json = metrics
            run.finished_at = utcnow()
            db.commit()
    except Exception as exc:
        db.rollback()
        run = db.get(RecommenderTrainingRun, run_id)
        if run is not None:
            run.status = "failed"
            run.phase = "failed"
            run.message = "训练失败，请查看错误信息。"
            run.error_message = str(exc)
            run.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


@router.post("/train")
def train_recommender(
    background_tasks: BackgroundTasks,
    training_device: str = Form("auto"),
    db: Session = Depends(get_db),
):
    requested_device = (training_device or "auto").strip().lower()
    if requested_device not in {"auto", "cpu", "cuda"}:
        return RedirectResponse("/recommender?message=" + quote_plus("训练设备选择无效"), status_code=303)
    hardware = gpu_status()
    if requested_device == "cuda" and not hardware["cuda_ready"]:
        return RedirectResponse(
            "/recommender?message=" + quote_plus(f"无法开始 CUDA 训练：{hardware['cuda_reason']}"),
            status_code=303,
        )
    active_run = db.scalar(
        select(RecommenderTrainingRun)
        .where(RecommenderTrainingRun.status.in_(["pending", "running"]))
        .order_by(RecommenderTrainingRun.created_at.desc())
        .limit(1)
    )
    if active_run is not None:
        return RedirectResponse(
            f"/recommender?run_id={active_run.id}&message=" + quote_plus("已有训练任务正在运行，请等待它完成"),
            status_code=303,
        )
    run = RecommenderTrainingRun(requested_device=requested_device)
    db.add(run)
    db.commit()
    db.refresh(run)
    background_tasks.add_task(_run_training_job, run.id, requested_device)
    return RedirectResponse(f"/recommender?run_id={run.id}", status_code=303)


@router.get("/training/{run_id}/status", response_class=JSONResponse)
def training_status(run_id: int, db: Session = Depends(get_db)):
    run = db.get(RecommenderTrainingRun, run_id)
    if run is None:
        return JSONResponse({"detail": "训练任务不存在"}, status_code=404)
    return JSONResponse(_training_payload(run))
