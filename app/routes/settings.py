from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..ai_provider import AIProviderError, discover_models, models_endpoint
from ..db import get_db
from ..image_search import DEFAULT_IMAGE_SEARCH_PROVIDER, IMAGE_SEARCH_PROVIDER_LABELS
from ..models import Setting
from ..repositories import get_settings, set_setting
from ..web import templates


router = APIRouter(prefix="/settings", tags=["settings"])
SECRET_KEYS = {"ai_chat_api_key", "ai_image_api_key"}
SETTING_KEYS = [
    "ai_chat_endpoint",
    "ai_chat_api_key",
    "ai_chat_model",
    "ai_image_endpoint",
    "ai_image_api_key",
    "ai_image_model",
    "image_search_provider",
    "preferred_keywords",
    "blocked_keywords",
]


def _masked(value: str) -> str:
    return "********" if value else ""


def _context(
    request: Request,
    db: Session,
    values: dict[str, str] | None = None,
    *,
    chat_models: list[str] | None = None,
    image_models: list[str] | None = None,
    model_message: str = "",
    model_error: str = "",
) -> dict:
    values = values or get_settings(db, SETTING_KEYS)
    if values.get("image_search_provider", "").lower() not in IMAGE_SEARCH_PROVIDER_LABELS:
        values["image_search_provider"] = DEFAULT_IMAGE_SEARCH_PROVIDER
    return {
        "request": request,
        "values": values,
        "masked": {key: _masked(values.get(key, "")) for key in SECRET_KEYS},
        "env_defaults": {
            key: bool(os.getenv(env))
            for key, env in {"ai_chat_api_key": "AI_CHAT_API_KEY", "ai_image_api_key": "AI_IMAGE_API_KEY"}.items()
        },
        "chat_models": chat_models or [],
        "image_models": image_models or [],
        "model_message": model_message,
        "model_error": model_error,
    }


@router.get("", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request=request, name="settings.html", context=_context(request, db))


@router.post("/models/{target}", response_class=HTMLResponse)
async def fetch_models(target: str, request: Request, db: Session = Depends(get_db)):
    if target not in {"chat", "image"}:
        raise HTTPException(status_code=404, detail="Unsupported model target")
    form = await request.form()
    stored = get_settings(db, SETTING_KEYS)
    values = {key: str(form.get(key, stored.get(key, ""))).strip() for key in SETTING_KEYS}
    key_name = f"ai_{target}_api_key"
    endpoint_name = f"ai_{target}_endpoint"
    submitted_key = values[key_name]
    actual_key = stored.get(key_name, "") if submitted_key in {"", "********"} else submitted_key
    values[key_name] = actual_key
    try:
        found = discover_models(values[endpoint_name], actual_key)
        message = f"已从 {models_endpoint(values[endpoint_name])} 找到 {len(found)} 个模型，请选择后保存。"
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_context(
                request,
                db,
                values,
                chat_models=found if target == "chat" else None,
                image_models=found if target == "image" else None,
                model_message=message,
            ),
        )
    except (AIProviderError, ValueError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="settings.html",
            context=_context(request, db, values, model_error=str(exc)),
            status_code=422,
        )


@router.post("")
def save_settings(
    ai_chat_endpoint: str = Form(""),
    ai_chat_api_key: str = Form(""),
    ai_chat_model: str = Form(""),
    ai_image_endpoint: str = Form(""),
    ai_image_api_key: str = Form(""),
    ai_image_model: str = Form(""),
    image_search_provider: str = Form(DEFAULT_IMAGE_SEARCH_PROVIDER),
    preferred_keywords: str = Form(""),
    blocked_keywords: str = Form(""),
    db: Session = Depends(get_db),
):
    values = {
        "ai_chat_endpoint": ai_chat_endpoint.strip(),
        "ai_chat_api_key": ai_chat_api_key.strip(),
        "ai_chat_model": ai_chat_model.strip(),
        "ai_image_endpoint": ai_image_endpoint.strip(),
        "ai_image_api_key": ai_image_api_key.strip(),
        "ai_image_model": ai_image_model.strip(),
        "image_search_provider": image_search_provider.strip().lower()
        if image_search_provider.strip().lower() in IMAGE_SEARCH_PROVIDER_LABELS
        else DEFAULT_IMAGE_SEARCH_PROVIDER,
        "preferred_keywords": preferred_keywords.strip(),
        "blocked_keywords": blocked_keywords.strip(),
    }
    for key, value in values.items():
        if key in SECRET_KEYS and (value == "********" or (not value and db.get(Setting, key) is not None)):
            continue
        set_setting(db, key, value)
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)
