"""Searchable library of stored transcripts."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import Analysis, Conversation, Task, Transcript, Video
from ..schemas import LibraryItem, LibraryPage, LibraryStats

router = APIRouter(tags=["library"])


def _excerpt(text: str, query: str, size: int = 200) -> str:
    flat = " ".join((text or "").split())
    if query:
        idx = flat.lower().find(query.lower())
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(flat), idx + len(query) + size - 100)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(flat) else ""
            return f"{prefix}{flat[start:end]}{suffix}"
    return flat[:size] + ("…" if len(flat) > size else "")


@router.get("/library", response_model=LibraryPage)
def search_library(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> LibraryPage:
    query = (
        db.query(Transcript, Task, Video)
        .join(Task, Transcript.task_id == Task.id)
        .join(Video, Task.video_id == Video.id)
    )
    keyword = q.strip()
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            or_(
                Transcript.text.ilike(like),
                Video.title.ilike(like),
                Video.uploader.ilike(like),
                Video.description.ilike(like),
            )
        )
    total = query.count()
    rows = (
        query.order_by(Transcript.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    items = [
        LibraryItem(
            task_id=task.id,
            transcript_id=transcript.id,
            title=video.title or "未命名视频",
            uploader=video.uploader,
            cover_url=video.cover_url,
            duration_sec=transcript.duration_sec,
            speaker_count=transcript.speaker_count,
            segment_count=transcript.segment_count,
            created_at=transcript.created_at,
            excerpt=_excerpt(transcript.text, keyword),
        )
        for transcript, task, video in rows
    ]
    return LibraryPage(items=items, total=total)


@router.get("/library/stats", response_model=LibraryStats)
def library_stats(db: Session = Depends(get_db)) -> LibraryStats:
    audio_files = 0
    audio_bytes = 0
    if settings.audio_dir.exists():
        for path in settings.audio_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() not in {".wav", ".part"}:
                audio_files += 1
                audio_bytes += path.stat().st_size

    return LibraryStats(
        tasks=db.query(func.count(Task.id)).scalar() or 0,
        transcripts=db.query(func.count(Transcript.id)).scalar() or 0,
        analyses=db.query(func.count(Analysis.id)).scalar() or 0,
        conversations=db.query(func.count(Conversation.id)).scalar() or 0,
        speakers=db.query(func.coalesce(func.sum(Transcript.speaker_count), 0)).scalar() or 0,
        audio_files=audio_files,
        audio_bytes=audio_bytes,
        total_duration_sec=float(
            db.query(func.coalesce(func.sum(Transcript.duration_sec), 0.0)).scalar() or 0
        ),
    )
