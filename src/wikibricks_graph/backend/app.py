"""FastAPI entry for the WikiBricks graph app.

Serves the React SPA static bundle at ``/`` and the API at ``/api``.
The frontend is built via ``cd ui && npm run build`` → ``ui/dist/``.
When the SPA dist isn't present (e.g. local backend-only dev), the
static-serving routes are skipped — the API still works.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.router import router

# Resolve to the ui/dist directory packaged next to the backend module.
# Layout in the deployed workspace:
#   <root>/
#     backend/app.py     ← __file__
#     ui/dist/index.html
_DIST_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "ui", "dist")
)
_DIST_ASSETS = os.path.join(_DIST_DIR, "assets")
_DIST_INDEX = os.path.join(_DIST_DIR, "index.html")


def create_app() -> FastAPI:
    app = FastAPI(title="wikibricks-graph", version="0.7.12")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)

    if os.path.isdir(_DIST_ASSETS):
        app.mount("/assets", StaticFiles(directory=_DIST_ASSETS), name="assets")

    if os.path.isfile(_DIST_INDEX):
        # SPA catch-all: any non-/api, non-/assets path returns index.html.
        # Must be registered AFTER the API router so /api/* still resolves.
        @app.get("/{full_path:path}")
        def serve_spa(full_path: str) -> FileResponse:
            # Don't intercept API paths — let FastAPI's normal 404 fire.
            if full_path.startswith("api/"):
                from fastapi import HTTPException
                raise HTTPException(status_code=404)
            return FileResponse(_DIST_INDEX)

    return app


app = create_app()
