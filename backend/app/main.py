"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import library, media, profiles, system, tasks, transcripts
from .config import settings
from .db import init_db
from .pipeline.pipeline import prepare_models_link
from .services.jobs import job_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    prepare_models_link()
    job_manager.start()
    yield
    job_manager.shutdown()


app = FastAPI(
    title="VideoMind · AI 视频解析工作台",
    description="Bilibili 视频 → 音频规范化 → 说话人分离转录 → AI 内容审校与讨论",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router, prefix="/api")
app.include_router(transcripts.router, prefix="/api")
app.include_router(library.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(media.router, prefix="/api")
app.include_router(profiles.router, prefix="/api")


@app.get("/api/health", include_in_schema=False)
def health_alias():
    return {"status": "ok", "version": system.VERSION}


@app.get("/api", include_in_schema=False)
def api_root():
    return {
        "name": "VideoMind API",
        "version": system.VERSION,
        "docs": "/api/docs",
    }


def _mount_frontend() -> None:
    dist = settings.frontend_dist
    if not dist.exists():
        @app.get("/", include_in_schema=False)
        def missing_frontend():
            return HTMLResponse(
                "<h1>VideoMind</h1><p>前端尚未构建。请在 frontend 目录运行 "
                "<code>npm install && npm run build</code>，或使用 <code>npm run dev</code> 进行开发。</p>",
                status_code=200,
            )
        return

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str, request: Request):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = dist / full_path
        if full_path and candidate.is_file() and dist in candidate.resolve().parents:
            return FileResponse(candidate)
        index = dist / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse({"detail": "frontend not built"}, status_code=404)


_mount_frontend()
