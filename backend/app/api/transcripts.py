"""Transcript, analysis and discussion endpoints."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session, joinedload, selectinload

from ..config import settings
from ..db import get_db, session_scope
from ..models import Analysis, Conversation, Message, Segment, Task, Transcript, Video
from ..pipeline.transcript import (
    FinalSegment,
    format_timestamp,
    render_context,
    select_relevant,
    to_markdown,
    to_plain,
    to_srt,
)
from ..schemas import (
    AnalysisOut,
    AnalysisRequest,
    ChatRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    MessageOut,
    MessageResponse,
    SegmentOut,
    TranscriptOut,
)
from ..services.llm import LLMClient, LLMError
from ..services.prompts import analysis_messages, chat_messages
from ..services.settings_store import get_runtime
from .sse import SSE_HEADERS, sse

router = APIRouter(tags=["transcripts"])

ANALYSIS_TITLES = {
    "review": "内容审校",
    "summary": "结构化摘要",
    "keypoints": "观点提炼",
    "custom": "自定义分析",
}


def _get_transcript(db: Session, transcript_id: str) -> Transcript:
    transcript = (
        db.query(Transcript)
        .options(
            selectinload(Transcript.segments),
            joinedload(Transcript.task).joinedload(Task.video),
        )
        .filter(Transcript.id == transcript_id)
        .one_or_none()
    )
    if transcript is None:
        raise HTTPException(status_code=404, detail="文稿不存在")
    return transcript


def _final_segments(transcript: Transcript) -> list[FinalSegment]:
    return [
        FinalSegment(
            start=seg.start_ms / 1000.0,
            end=seg.end_ms / 1000.0,
            speaker=seg.speaker,
            text=seg.text,
        )
        for seg in transcript.segments
    ]


@router.get("/transcripts/{transcript_id}", response_model=TranscriptOut)
def get_transcript(transcript_id: str, db: Session = Depends(get_db)) -> TranscriptOut:
    transcript = _get_transcript(db, transcript_id)
    data = TranscriptOut.model_validate(transcript)
    data.task_id = transcript.task_id
    data.segments = [SegmentOut.model_validate(seg) for seg in transcript.segments]
    return data


@router.get("/transcripts/{transcript_id}/export")
def export_transcript(
    transcript_id: str,
    format: Literal["md", "txt", "srt", "json"] = Query(default="md"),
    db: Session = Depends(get_db),
):
    transcript = _get_transcript(db, transcript_id)
    segments = _final_segments(transcript)
    video: Video = transcript.task.video
    title = video.title or "转录文稿"
    engine = f"{transcript.asr_backend} / {transcript.asr_model}"

    if format == "srt":
        content = to_srt(segments)
        suffix, media = ".srt", "application/x-subrip"
    elif format == "txt":
        content = to_plain(segments)
        suffix, media = ".txt", "text/plain; charset=utf-8"
    elif format == "json":
        import json

        content = json.dumps(
            {
                "title": title,
                "url": video.url,
                "duration": transcript.duration_sec,
                "language": transcript.language,
                "speaker_count": transcript.speaker_count,
                "segments": [
                    {
                        "start": seg.start,
                        "end": seg.end,
                        "speaker": seg.speaker,
                        "text": seg.text,
                    }
                    for seg in segments
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        suffix, media = ".json", "application/json; charset=utf-8"
    else:
        content = to_markdown(
            segments,
            title=title,
            url=video.url,
            duration=transcript.duration_sec,
            speaker_count=transcript.speaker_count,
            engine=engine,
        )
        suffix, media = ".md", "text/markdown; charset=utf-8"

    safe_title = _safe_filename(title)
    filename = quote(f"{safe_title}{suffix}")
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}
    return Response(content=content, media_type=media, headers=headers)


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", name).strip(" .")
    return (cleaned or "transcript")[:80]


# ---------------------------------------------------------------------------
# analyses
# ---------------------------------------------------------------------------
@router.get("/transcripts/{transcript_id}/analyses", response_model=list[AnalysisOut])
def list_analyses(transcript_id: str, db: Session = Depends(get_db)) -> list[AnalysisOut]:
    _get_transcript(db, transcript_id)
    rows = (
        db.query(Analysis)
        .filter(Analysis.transcript_id == transcript_id)
        .order_by(Analysis.created_at.desc())
        .all()
    )
    return [AnalysisOut.model_validate(row) for row in rows]


@router.post("/transcripts/{transcript_id}/analyses/stream")
def stream_analysis(
    transcript_id: str,
    payload: AnalysisRequest,
    db: Session = Depends(get_db),
):
    transcript = _get_transcript(db, transcript_id)
    runtime = get_runtime(db)
    client = LLMClient(runtime)
    if not client.configured():
        raise HTTPException(
            status_code=400,
            detail="尚未配置大模型 API Key，请先到「设置」中填写 DeepSeek / OpenAI / 其他兼容接口",
        )

    segments = _final_segments(transcript)
    video: Video = transcript.task.video
    messages = analysis_messages(
        kind=payload.kind,
        title=video.title,
        uploader=video.uploader,
        duration=transcript.duration_sec,
        url=video.url,
        segments=segments,
        instructions=payload.instructions or "",
    )
    title = payload.title or ANALYSIS_TITLES.get(payload.kind, "AI 分析")
    model = payload.model or runtime.llm_model
    kind = payload.kind

    def generate():
        parts: list[str] = []
        try:
            for delta in client.stream(messages, model=model):
                if delta.kind == "reasoning":
                    # Do not mix chain-of-thought with the final answer.
                    yield sse({"reasoning": delta.text})
                else:
                    parts.append(delta.text)
                    yield sse({"delta": delta.text})
        except LLMError as exc:
            yield sse({"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            yield sse({"error": f"分析失败：{exc}"})
            return

        content = "".join(parts).strip()
        if not content:
            yield sse({"error": "模型没有返回内容，请稍后重试"})
            return
        with session_scope() as session:
            row = Analysis(
                transcript_id=transcript_id,
                kind=kind,
                title=title,
                content=content,
                model=model,
            )
            session.add(row)
            session.flush()
            analysis_id = row.id
            created = row.created_at
        yield sse(
            {
                "done": True,
                "analysis": {
                    "id": analysis_id,
                    "transcript_id": transcript_id,
                    "kind": kind,
                    "title": title,
                    "content": content,
                    "model": model,
                    "created_at": created.isoformat() if created else datetime.now(timezone.utc).isoformat(),
                },
            }
        )

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.delete("/analyses/{analysis_id}", response_model=MessageResponse)
def delete_analysis(analysis_id: str, db: Session = Depends(get_db)) -> MessageResponse:
    analysis = db.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    db.delete(analysis)
    db.commit()
    return MessageResponse(message="分析记录已删除")


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------
@router.get("/transcripts/{transcript_id}/conversations", response_model=list[ConversationOut])
def list_conversations(
    transcript_id: str, db: Session = Depends(get_db)
) -> list[ConversationOut]:
    _get_transcript(db, transcript_id)
    conversations = (
        db.query(Conversation)
        .options(selectinload(Conversation.messages))
        .filter(Conversation.transcript_id == transcript_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    result: list[ConversationOut] = []
    for conv in conversations:
        item = ConversationOut.model_validate(conv)
        item.message_count = len(conv.messages)
        item.last_message = (conv.messages[-1].content[:120] if conv.messages else "")
        result.append(item)
    return result


@router.post(
    "/transcripts/{transcript_id}/conversations",
    response_model=ConversationOut,
    status_code=201,
)
def create_conversation(
    transcript_id: str, payload: ConversationCreate, db: Session = Depends(get_db)
) -> ConversationOut:
    _get_transcript(db, transcript_id)
    conv = Conversation(
        transcript_id=transcript_id,
        title=(payload.title or "").strip() or "新的讨论",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationOut.model_validate(conv)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationDetail:
    conv = (
        db.query(Conversation)
        .options(selectinload(Conversation.messages))
        .filter(Conversation.id == conversation_id)
        .one_or_none()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="讨论不存在")
    detail = ConversationDetail.model_validate(conv)
    detail.messages = [MessageOut.model_validate(m) for m in conv.messages]
    detail.message_count = len(conv.messages)
    detail.last_message = conv.messages[-1].content[:120] if conv.messages else ""
    return detail


@router.delete("/conversations/{conversation_id}", response_model=MessageResponse)
def delete_conversation(conversation_id: str, db: Session = Depends(get_db)) -> MessageResponse:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="讨论不存在")
    db.delete(conv)
    db.commit()
    return MessageResponse(message="讨论已删除")


@router.post("/conversations/{conversation_id}/messages/stream")
def stream_message(
    conversation_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
):
    conv = (
        db.query(Conversation)
        .options(joinedload(Conversation.transcript).selectinload(Transcript.segments))
        .filter(Conversation.id == conversation_id)
        .one_or_none()
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="讨论不存在")
    runtime = get_runtime(db)
    client = LLMClient(runtime)
    if not client.configured():
        raise HTTPException(status_code=400, detail="尚未配置大模型 API Key，请先到「设置」中填写")

    transcript = conv.transcript
    task = db.get(Task, transcript.task_id)
    video = db.get(Video, task.video_id) if task else None
    segments = _final_segments(transcript)
    question = payload.content.strip()

    history = [{"role": m.role, "content": m.content} for m in conv.messages]
    user_message = Message(conversation_id=conv.id, role="user", content=question)
    db.add(user_message)
    if conv.title in {"", "新的讨论"}:
        conv.title = question[:28] + ("…" if len(question) > 28 else "")
    db.commit()
    db.refresh(user_message)

    if len(render_context(segments)) > 60_000:
        segments_for_context = select_relevant(segments, question, budget_chars=55_000)
    else:
        segments_for_context = segments

    messages = chat_messages(
        title=video.title if video else "",
        uploader=video.uploader if video else "",
        duration=transcript.duration_sec,
        segments=segments_for_context,
        history=history,
        question=question,
    )
    model = runtime.llm_model

    def generate():
        parts: list[str] = []
        yield sse({"user_message": MessageOut.model_validate(user_message).model_dump(mode="json")})
        try:
            for delta in client.stream(messages, model=model):
                if delta.kind == "reasoning":
                    yield sse({"reasoning": delta.text})
                else:
                    parts.append(delta.text)
                    yield sse({"delta": delta.text})
        except LLMError as exc:
            yield sse({"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            yield sse({"error": f"生成回复失败：{exc}"})
            return

        content = "".join(parts).strip()
        if not content:
            yield sse({"error": "模型没有返回内容，请稍后重试"})
            return
        with session_scope() as session:
            assistant = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                model=model,
            )
            session.add(assistant)
            conv_row = session.get(Conversation, conversation_id)
            if conv_row is not None:
                conv_row.updated_at = datetime.now(timezone.utc)
            session.flush()
            message_payload = {
                "id": assistant.id,
                "role": "assistant",
                "content": content,
                "model": model,
                "created_at": assistant.created_at.isoformat() if assistant.created_at else "",
            }
        yield sse({"done": True, "message": message_payload})

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)
