"""FastAPI entry for the WikiBricks graph app. Filled in by Task 6."""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the FastAPI app. Routes wired in Task 6 (router.py)."""
    app = FastAPI(title="wikibricks-graph", version="0.7.12")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
