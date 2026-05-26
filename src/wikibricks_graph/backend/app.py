"""FastAPI entry for the WikiBricks graph app."""

from __future__ import annotations

from fastapi import FastAPI

from backend.router import router


def create_app() -> FastAPI:
    app = FastAPI(title="wikibricks-graph", version="0.7.12")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
