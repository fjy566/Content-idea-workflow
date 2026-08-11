from __future__ import annotations

import logging
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
    app = FastAPI(title=settings.app_name)

    @app.on_event("startup")
    def startup() -> None:
        init_db()
        start_scheduler()

    @app.on_event("shutdown")
    def shutdown() -> None:
        stop_scheduler()

    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    app.mount("/media", StaticFiles(directory=str(settings.data_dir)), name="media")
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
