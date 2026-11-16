"""Task, probe and media endpoints."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload, selectinload

from ..config import settings
from ..db import get_db
from ..models import Task, TaskEvent, Video, utcnow
from ..pipeline import downloader
from ..pipeline.pipeline import STAGE_LABELS
from ..schemas import (
    EventOut,
    MessageResponse,
    ProbeResult,
    TaskCreate,
    TaskDetail,
    TaskListOut,
    TaskOut,
)
from ..services.jobs import job_manager

router = APIRouter(tags=["tasks"])

AUDIO_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
}


def _get_task(db: Session, task_id: str) -> Task:
    task = (
        db.query(Task)
        .options(joinedload(Task.video), selectinload(Task.transcript))
        .filter(Task.id == task_id)
        .one_or_none()
    )
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _upsert_video(db: Session, info: dict) -> Video:
    video = (
        db.query(Video)
        .filter(
            Video.platform == info["platform"],
            Video.remote_id == info["remote_id"],
            Video.page == int(info.get("page") or 1),
        )
        .one_or_none()
    )
    if video is None:
        video = Video(
            platform=info["platform"],
            remote_id=info["remote_id"],
            page=int(info.get("page") or 1),
            url=info["url"],
        )
        db.add(video)
    video.url = info["url"] or video.url
    video.title = info.get("title") or video.title
    video.uploader = info.get("uploader") or video.uploader
    video.cover_url = info.get("cover_url") or video.cover_url
    video.duration_sec = float(info.get("duration_sec") or video.duration_sec or 0)
    video.description = info.get("description") or video.description
    if info.get("published_at"):
        video.published_at = info["published_at"]
    db.flush()
    return video


@router.post("/videos/probe", response_model=ProbeResult)
def probe_video(payload: dict = Body(...), db: Session = Depends(get_db)) -> ProbeResult:
    url = str(payload.get("url") or "")
    try:
        info = downloader.probe(url, proxy=settings.proxy, cookies_file=settings.cookies_file)
    except downloader.DownloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    video = (
        db.query(Video)
        .filter(
            Video.platform == info["platform"],
            Video.remote_id == info["remote_id"],
            Video.page == int(info.get("page") or 1),
        )
        .one_or_none()
    )
    existing: list[TaskOut] = []
    if video:
        tasks = (
            db.query(Task)
            .options(joinedload(Task.video), selectinload(Task.transcript))
            .filter(Task.video_id == video.id)
            .order_by(Task.created_at.desc())
            .limit(10)
            .all()
        )
        existing = [TaskOut.model_validate(task) for task in tasks]

    return ProbeResult(**info, existing_tasks=existing)


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskOut:
    try:
        info = downloader.probe(
            payload.url, proxy=settings.proxy, cookies_file=settings.cookies_file
        )
    except downloader.DownloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    video = _upsert_video(db, info)
    options = payload.options.model_dump()
    task = Task(
        video_id=video.id,
        options=options,
        delete_audio_after=payload.options.delete_audio,
        status="queued",
        stage="queued",
        message="等待处理",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    db.add(TaskEvent(task_id=task.id, stage="queued", message="任务已创建"))
    db.commit()

    job_manager.submit(task.id, already_queued=True)
    task = _get_task(db, task.id)
    return TaskOut.model_validate(task)


@router.get("/tasks", response_model=TaskListOut)
def list_tasks(
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> TaskListOut:
    query = db.query(Task).options(joinedload(Task.video), selectinload(Task.transcript))
    if status:
        query = query.filter(Task.status == status)
    total = query.count()
    tasks = query.order_by(Task.created_at.desc()).offset(offset).limit(limit).all()
    return TaskListOut(items=[TaskOut.model_validate(task) for task in tasks], total=total)


@router.get("/tasks/{task_id}", response_model=TaskDetail)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskDetail:
    task = _get_task(db, task_id)
    events = (
        db.query(TaskEvent)
        .filter(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.id.desc())
        .limit(200)
        .all()
    )
    detail = TaskDetail.model_validate(task)
    detail.events = [EventOut.model_validate(event) for event in reversed(events)]
    return detail


@router.get("/tasks/{task_id}/events", response_model=list[EventOut])
def get_events(
    task_id: str,
    after_id: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[EventOut]:
    _get_task(db, task_id)
    events = (
        db.query(TaskEvent)
        .filter(TaskEvent.task_id == task_id, TaskEvent.id > after_id)
        .order_by(TaskEvent.id)
        .limit(300)
        .all()
    )
    return [EventOut.model_validate(event) for event in events]


@router.post("/tasks/{task_id}/pause", response_model=TaskOut)
def pause_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    task = _get_task(db, task_id)
    if task.status in {"completed", "failed", "canceled"}:
        raise HTTPException(status_code=400, detail="任务已经结束，无法暂停")
    if task.status == "paused":
        return TaskOut.model_validate(task)
    task.pause_requested = True
    task.message = "正在暂停…"
    if not job_manager.is_running(task.id):
        task.status = "paused"
        task.pause_requested = False
        task.message = "已暂停"
    db.add(TaskEvent(task_id=task.id, stage=task.stage, message="收到暂停请求", level="warn"))
    db.commit()
    return TaskOut.model_validate(_get_task(db, task_id))


@router.post("/tasks/{task_id}/resume", response_model=TaskOut)
def resume_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    task = _get_task(db, task_id)
    if task.status != "paused":
        raise HTTPException(status_code=400, detail="只有已暂停的任务可以继续")
    task.status = "queued"
    task.pause_requested = False
    task.message = "等待继续"
    task.error = None
    task.finished_at = None
    db.add(TaskEvent(task_id=task.id, stage=task.stage, message="用户请求继续任务"))
    db.commit()
    job_manager.submit(task.id, already_queued=True)
    return TaskOut.model_validate(_get_task(db, task_id))


@router.post("/tasks/{task_id}/cancel", response_model=TaskOut)
def cancel_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    task = _get_task(db, task_id)
    if task.status in {"completed", "failed", "canceled"}:
        raise HTTPException(status_code=400, detail="任务已经结束，无法终止")
    running = job_manager.is_running(task.id)
    if not running and task.status in {"queued", "paused"}:
        task.status = "canceled"
        task.stage = "canceled"
        task.cancel_requested = False
        task.pause_requested = False
        task.resume_stage = ""
        task.message = "任务已终止"
        task.finished_at = utcnow()
    else:
        task.cancel_requested = True
        task.pause_requested = False
        task.message = "正在终止…"
    db.add(TaskEvent(task_id=task.id, stage=task.stage, message="收到终止请求", level="warn"))
    db.commit()
    return TaskOut.model_validate(_get_task(db, task_id))


@router.post("/tasks/{task_id}/retry", response_model=TaskOut)
def retry_task(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    task = _get_task(db, task_id)
    if job_manager.is_running(task.id):
        raise HTTPException(status_code=409, detail="任务正在运行中")
    if task.transcript is not None:
        db.delete(task.transcript)
        db.flush()
    task.status = "queued"
    task.stage = "queued"
    task.progress = 0
    task.message = "等待重新处理"
    task.error = None
    task.cancel_requested = False
    task.pause_requested = False
    task.resume_stage = ""
    task.started_at = None
    task.finished_at = None
    task.audio_deleted = False
    db.add(TaskEvent(task_id=task.id, stage="queued", message="用户请求重新解析"))
    db.commit()
    job_manager.submit(task.id, already_queued=True)
    return TaskOut.model_validate(_get_task(db, task_id))


@router.delete("/tasks/{task_id}", response_model=MessageResponse)
def delete_task(task_id: str, db: Session = Depends(get_db)) -> MessageResponse:
    task = _get_task(db, task_id)
    if job_manager.is_running(task.id):
        raise HTTPException(status_code=409, detail="任务正在运行，请先终止")
    workdir = settings.audio_dir / task.id
    shutil.rmtree(workdir, ignore_errors=True)
    db.delete(task)
    db.commit()
    return MessageResponse(message="任务已删除")


@router.delete("/tasks/{task_id}/audio", response_model=TaskOut)
def delete_audio(task_id: str, db: Session = Depends(get_db)) -> TaskOut:
    task = _get_task(db, task_id)
    if job_manager.is_running(task.id):
        raise HTTPException(status_code=409, detail="任务正在运行，暂时不能删除音频")
    workdir = settings.audio_dir / task.id
    shutil.rmtree(workdir, ignore_errors=True)
    for path in (task.audio_path, task.processed_path):
        if path:
            Path(path).unlink(missing_ok=True)
    task.audio_deleted = True
    task.audio_path = None
    task.processed_path = None
    task.message = "音频已删除，文稿保留"
    db.add(TaskEvent(task_id=task.id, stage="done", message="用户删除了音频文件"))
    db.commit()
    return TaskOut.model_validate(_get_task(db, task_id))


@router.get("/tasks/{task_id}/audio")
def get_audio(
    task_id: str,
    variant: str = Query(default="enhanced", pattern="^(enhanced|source)$"),
    db: Session = Depends(get_db),
):
    task = _get_task(db, task_id)
    if task.audio_deleted:
        raise HTTPException(status_code=404, detail="音频已删除")
    path = task.processed_path if variant == "enhanced" else task.audio_path
    if variant == "enhanced" and not path:
        path = task.audio_path
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    media_type = AUDIO_MEDIA_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@router.get("/stages")
def get_stages() -> dict[str, str]:
    return STAGE_LABELS
