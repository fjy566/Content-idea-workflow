from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .routes import admin, articles, dashboard, recommender, settings as settings_routes, sources, topics
from .scheduler import start_scheduler, stop_scheduler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        start_scheduler()
        try:
            yield
        finally:
            stop_scheduler()

    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response

    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    # Only expose downloaded/generated images. Mounting the whole data
    # directory would make app.db and model artifacts downloadable over HTTP.
    app.mount("/media/images", StaticFiles(directory=str(settings.image_dir)), name="media-images")
    app.include_router(dashboard.router)
    app.include_router(sources.router)
    app.include_router(settings_routes.router)
    app.include_router(articles.router)
    app.include_router(topics.router)
    app.include_router(recommender.router)
    app.include_router(admin.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
