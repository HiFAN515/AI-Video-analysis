"""Settings and system status endpoints."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Analysis, Conversation, Task, Transcript
from ..pipeline import audio, diarization
from ..schemas import (
    HealthOut,
    MessageResponse,
    SettingsOut,
    SettingsUpdate,
    TestLLMRequest,
    TestResult,
)
from ..services.jobs import job_manager
from ..services.llm import LLMClient
from ..services.settings_store import apply_patch, get_runtime, save_runtime

router = APIRouter(tags=["system"])

VERSION = "1.0.0"
KNOWN_ASR_MODELS = [
    {"id": "tiny", "label": "Tiny · 最快，准确率低（约 75MB）"},
    {"id": "base", "label": "Base · 快，适合清晰录音（约 145MB）"},
    {"id": "small", "label": "Small · 速度与准确率平衡（约 480MB）"},
    {"id": "medium", "label": "Medium · 更准，速度较慢（约 1.5GB）"},
    {"id": "large-v3", "label": "Large v3 · 最准，CPU 上较慢（约 3GB）"},
    {"id": "distil-large-v3", "label": "Distil Large v3 · 英文推荐（约 1.5GB）"},
]


@router.get("/settings", response_model=SettingsOut)
def read_settings(db: Session = Depends(get_db)) -> SettingsOut:
    return get_runtime(db).to_public()


@router.put("/settings", response_model=SettingsOut)
def update_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsOut:
    runtime = apply_patch(db, payload)
    return runtime.to_public()


@router.post("/settings/test-llm", response_model=TestResult)
def test_llm(payload: TestLLMRequest, db: Session = Depends(get_db)) -> TestResult:
    runtime = get_runtime(db)
    key = payload.api_key.strip() if payload.api_key else runtime.llm_api_key
    client = LLMClient(runtime)
    ok, message, latency = client.test(
        base_url=payload.base_url or runtime.llm_base_url,
        model=payload.model or runtime.llm_model,
        api_key=key,
    )
    return TestResult(ok=ok, message=message, latency_ms=latency, sample="正常" if ok else "")


@router.get("/system/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    runtime = get_runtime(db)
    llm_ready = bool(runtime.llm_api_key) or "localhost" in runtime.llm_base_url or "127.0.0.1" in runtime.llm_base_url
    counts = {
        "tasks": db.query(func.count(Task.id)).scalar() or 0,
        "transcripts": db.query(func.count(Transcript.id)).scalar() or 0,
        "analyses": db.query(func.count(Analysis.id)).scalar() or 0,
        "conversations": db.query(func.count(Conversation.id)).scalar() or 0,
    }
    return HealthOut(
        status="ok",
        version=VERSION,
        ffmpeg=audio.ffmpeg_available(),
        demucs=audio.demucs_available(),
        asr_backend=runtime.asr_backend,
        llm_configured=llm_ready,
        diarization_ready=diarization.models_ready(),
        features={
            "cuda": audio.cuda_available(),
            "gpu_present": audio.gpu_present(),
            "bgm_separation": audio.demucs_available(),
            "denoise": True,
            "diarization": diarization.models_ready(),
            "cloud_asr": bool(runtime.cloud_asr_api_key),
            "llm": llm_ready,
        },
        counts=counts,
    )


@router.get("/system/info")
def system_info(db: Session = Depends(get_db)) -> dict:
    runtime = get_runtime(db)
    usage = shutil.disk_usage(str(settings.data_dir))
    return {
        "version": VERSION,
        "python": sys.version.split()[0],
        "paths": {
            "data": str(settings.data_dir),
            "models": str(settings.model_dir),
            "audio": str(settings.audio_dir),
            "db": str(settings.db_path),
        },
        "disk": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        },
        "asr": {
            "backend": runtime.asr_backend,
            "model": runtime.asr_model,
            "device": runtime.asr_device,
            "compute_type": runtime.asr_compute_type,
        },
        "gpu": {
            "available": audio.cuda_available(),
            "present": audio.gpu_present(),
            "name": audio.gpu_name(),
            "audio_device": runtime.audio_device,
        },
        "queue": {
            "max_workers": settings.max_concurrent_jobs,
        },
    }


@router.get("/system/asr-models")
def asr_models(db: Session = Depends(get_db)) -> dict:
    runtime = get_runtime(db)
    local = []
    asr_dir = settings.model_dir / "asr"
    if asr_dir.exists():
        for entry in sorted(asr_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name.startswith("models--"):
                continue
            size = sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())
            local.append(
                {
                    "id": entry.name,
                    "label": f"{entry.name} · 本地模型",
                    "size": size,
                    "ready": (entry / "model.bin").exists(),
                }
            )
    known = [dict(item) for item in KNOWN_ASR_MODELS]
    existing = {item["id"] for item in known}
    known.extend(item for item in local if item["id"] not in existing)
    return {"current": runtime.asr_model, "models": known, "local": local}


@router.post("/system/cleanup", response_model=MessageResponse)
def cleanup_orphans(db: Session = Depends(get_db)) -> MessageResponse:
    task_ids = {row[0] for row in db.query(Task.id).all()}
    removed = 0
    freed = 0
    if settings.audio_dir.exists():
        for entry in settings.audio_dir.iterdir():
            if entry.is_dir() and entry.name not in task_ids:
                size = sum(p.stat().st_size for p in entry.rglob("*") if p.is_file())
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
                freed += size
    mb = freed / 1024 / 1024
    return MessageResponse(message=f"清理了 {removed} 个无主音频目录，释放 {mb:.1f} MB")


@router.post("/system/queue/pause", response_model=MessageResponse)
def queue_status() -> MessageResponse:
    return MessageResponse(message=f"当前最多并发 {settings.max_concurrent_jobs} 个任务")
