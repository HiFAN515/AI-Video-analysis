"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# videos / tasks
# ---------------------------------------------------------------------------
class VideoOut(ORMModel):
    id: int
    platform: str
    remote_id: str
    page: int
    url: str
    title: str
    uploader: str
    cover_url: str
    duration_sec: float
    description: str
    published_at: datetime | None
    created_at: datetime


class TranscriptMeta(ORMModel):
    id: str
    language: str
    duration_sec: float
    segment_count: int
    speaker_count: int
    asr_backend: str
    asr_model: str
    created_at: datetime


class EventOut(ORMModel):
    id: int
    created_at: datetime
    stage: str
    level: str
    message: str
    progress: int | None


class TaskOut(ORMModel):
    id: str
    status: str
    stage: str
    progress: int
    message: str
    options: dict[str, Any]
    error: str | None
    audio_available: bool
    enhanced_available: bool
    audio_deleted: bool
    delete_audio_after: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    video: VideoOut
    transcript: TranscriptMeta | None


class TaskDetail(TaskOut):
    events: list[EventOut] = Field(default_factory=list)


class TaskOptions(BaseModel):
    bgm_separation: bool = True
    denoise: bool = True
    denoise_strength: float = Field(default=0.85, ge=0.0, le=1.0)
    diarization: bool = True
    max_speakers: int = Field(default=0, ge=0, le=12)
    language: str = "auto"
    asr_model: str | None = None
    asr_backend: str | None = None
    delete_audio: bool = False


class TaskCreate(BaseModel):
    url: str = Field(min_length=4, max_length=2048)
    options: TaskOptions = Field(default_factory=TaskOptions)


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------
class ProbeResult(BaseModel):
    url: str
    platform: str
    remote_id: str
    page: int
    title: str
    uploader: str
    cover_url: str
    duration_sec: float
    description: str
    published_at: datetime | None
    existing_tasks: list[TaskOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# transcripts
# ---------------------------------------------------------------------------
class SegmentOut(ORMModel):
    idx: int
    start_ms: int
    end_ms: int
    speaker: str
    text: str


class TranscriptOut(TranscriptMeta):
    text: str
    segments: list[SegmentOut] = Field(default_factory=list)
    task_id: str


class AnalysisOut(ORMModel):
    id: str
    transcript_id: str
    kind: str
    title: str
    content: str
    model: str
    created_at: datetime


class AnalysisRequest(BaseModel):
    kind: Literal["review", "summary", "keypoints", "custom"] = "review"
    instructions: str = ""
    model: str | None = None
    title: str | None = None


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------
class MessageOut(ORMModel):
    id: int
    role: str
    content: str
    model: str
    created_at: datetime


class ConversationOut(ORMModel):
    id: str
    transcript_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    last_message: str = ""


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    title: str = ""


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------
class LibraryItem(BaseModel):
    task_id: str
    transcript_id: str
    title: str
    uploader: str
    cover_url: str
    duration_sec: float
    speaker_count: int
    segment_count: int
    created_at: datetime
    excerpt: str


class LibraryPage(BaseModel):
    items: list[LibraryItem]
    total: int


class LibraryStats(BaseModel):
    tasks: int
    transcripts: int
    analyses: int
    conversations: int
    speakers: int
    audio_files: int
    audio_bytes: int
    total_duration_sec: float


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
class LLMSettings(BaseModel):
    base_url: str
    model: str
    temperature: float
    api_key_set: bool
    api_key_hint: str


class ASRSettings(BaseModel):
    backend: str
    model: str
    device: str
    compute_type: str
    language: str
    cloud_base_url: str
    cloud_model: str
    cloud_api_key_set: bool
    cloud_api_key_hint: str


class AudioDefaults(BaseModel):
    bgm_separation: bool
    denoise: bool
    diarization: bool
    max_speakers: int
    threshold: float
    delete_audio: bool
    device: str = "auto"


class SettingsOut(BaseModel):
    llm: LLMSettings
    asr: ASRSettings
    audio: AudioDefaults
    paths: dict[str, str]


class SettingsUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    llm_api_key: str | None = None
    clear_llm_api_key: bool = False

    asr_backend: Literal["local", "cloud"] | None = None
    asr_model: str | None = None
    asr_device: str | None = None
    asr_compute_type: str | None = None
    asr_language: str | None = None
    cloud_asr_base_url: str | None = None
    cloud_asr_model: str | None = None
    cloud_asr_api_key: str | None = None
    clear_cloud_asr_api_key: bool = False

    audio_device: Literal["auto", "cpu", "cuda"] | None = None
    bgm_separation: bool | None = None
    denoise: bool | None = None
    diarization: bool | None = None
    diarization_max_speakers: int | None = Field(default=None, ge=0, le=12)
    diarization_threshold: float | None = Field(default=None, ge=0.1, le=0.95)
    default_delete_audio: bool | None = None

    @field_validator("llm_base_url", "cloud_asr_base_url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("地址必须以 http:// 或 https:// 开头")
        return value


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    version: str
    ffmpeg: bool
    demucs: bool
    asr_backend: str
    llm_configured: bool
    diarization_ready: bool
    features: dict[str, bool]
    counts: dict[str, int]


class TestLLMRequest(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class TestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
    sample: str = ""


class MessageResponse(BaseModel):
    ok: bool = True
    message: str = ""


# ---------------------------------------------------------------------------
# saved LLM profiles
# ---------------------------------------------------------------------------
class LLMProfileOut(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    temperature: float
    api_key_set: bool
    api_key_hint: str
    is_active: bool = False
    created_at: datetime
    updated_at: datetime


class LLMProfileCreate(BaseModel):
    name: str = ""
    base_url: str
    model: str
    api_key: str = ""
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("地址必须以 http:// 或 https:// 开头")
        return value
