from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..recommender import recommender_status, train_model
from ..web import templates


router = APIRouter(prefix="/recommender", tags=["recommender"])


@router.get("", response_class=HTMLResponse)
def recommender_page(request: Request, message: str = "", db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request=request,
        name="recommender.html",
        context={"status": recommender_status(db), "message": message},
    )


@router.post("/train")
def train_recommender(db: Session = Depends(get_db)):
    try:
        metrics = train_model(db)
        message = f"训练完成：{metrics['samples']} 个真实反馈样本"
    except ValueError as exc:
        message = str(exc)
    return RedirectResponse(f"/recommender?message={quote_plus(message)}", status_code=303)
