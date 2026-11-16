"""Runtime-editable settings persisted in SQLite."""
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..models import LLMProfile, Setting
from ..schemas import (
    ASRSettings,
    AudioDefaults,
    LLMSettings,
    SettingsOut,
    SettingsUpdate,
)

SETTINGS_KEY = "runtime"


class RuntimeSettings(BaseModel):
    llm_base_url: str = settings.llm_base_url
    llm_api_key: str = settings.llm_api_key
    llm_model: str = settings.llm_model
    llm_temperature: float = settings.llm_temperature

    asr_backend: str = settings.asr_backend
    asr_model: str = settings.asr_model
    asr_device: str = settings.asr_device
    asr_compute_type: str = settings.asr_compute_type
    asr_language: str = settings.asr_language
    cloud_asr_base_url: str = settings.cloud_asr_base_url
    cloud_asr_api_key: str = settings.cloud_asr_api_key
    cloud_asr_model: str = settings.cloud_asr_model

    audio_device: str = settings.audio_device
    bgm_separation: bool = settings.bgm_separation
    denoise: bool = settings.denoise
    diarization: bool = settings.diarization_enabled
    diarization_max_speakers: int = settings.diarization_max_speakers
    diarization_threshold: float = settings.diarization_threshold
    default_delete_audio: bool = settings.default_delete_audio

    def to_public(self) -> SettingsOut:
        return SettingsOut(
            llm=LLMSettings(
                base_url=self.llm_base_url,
                model=self.llm_model,
                temperature=self.llm_temperature,
                api_key_set=bool(self.llm_api_key),
                api_key_hint=_mask(self.llm_api_key),
            ),
            asr=ASRSettings(
                backend=self.asr_backend,
                model=self.asr_model,
                device=self.asr_device,
                compute_type=self.asr_compute_type,
                language=self.asr_language,
                cloud_base_url=self.cloud_asr_base_url,
                cloud_model=self.cloud_asr_model,
                cloud_api_key_set=bool(self.cloud_asr_api_key),
                cloud_api_key_hint=_mask(self.cloud_asr_api_key),
            ),
            audio=AudioDefaults(
                bgm_separation=self.bgm_separation,
                denoise=self.denoise,
                diarization=self.diarization,
                max_speakers=self.diarization_max_speakers,
                threshold=self.diarization_threshold,
                delete_audio=self.default_delete_audio,
                device=self.audio_device,
            ),
            paths={
                "data_dir": str(settings.data_dir),
                "model_dir": str(settings.model_dir),
                "db": str(settings.db_path),
                "audio": str(settings.audio_dir),
            },
        )


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


def get_runtime(db: Session) -> RuntimeSettings:
    row = db.get(Setting, SETTINGS_KEY)
    data = row.value if row and isinstance(row.value, dict) else {}
    allowed = set(RuntimeSettings.model_fields)
    clean = {k: v for k, v in data.items() if k in allowed}
    return RuntimeSettings(**clean)


def save_runtime(db: Session, runtime: RuntimeSettings) -> RuntimeSettings:
    row = db.get(Setting, SETTINGS_KEY)
    if row is None:
        row = Setting(key=SETTINGS_KEY, value=runtime.model_dump())
        db.add(row)
    else:
        row.value = runtime.model_dump()
    db.commit()
    return runtime


def apply_patch(db: Session, patch: SettingsUpdate) -> RuntimeSettings:
    runtime = get_runtime(db)
    data = runtime.model_dump()

    mapping = {
        "llm_base_url": "llm_base_url",
        "llm_model": "llm_model",
        "llm_temperature": "llm_temperature",
        "asr_backend": "asr_backend",
        "asr_model": "asr_model",
        "asr_device": "asr_device",
        "asr_compute_type": "asr_compute_type",
        "asr_language": "asr_language",
        "cloud_asr_base_url": "cloud_asr_base_url",
        "cloud_asr_model": "cloud_asr_model",
        "audio_device": "audio_device",
        "bgm_separation": "bgm_separation",
        "denoise": "denoise",
        "diarization": "diarization",
        "diarization_max_speakers": "diarization_max_speakers",
        "diarization_threshold": "diarization_threshold",
        "default_delete_audio": "default_delete_audio",
    }
    payload = patch.model_dump(exclude_unset=True)
    for src, dst in mapping.items():
        if src in payload and payload[src] is not None:
            data[dst] = payload[src]

    if patch.clear_llm_api_key:
        data["llm_api_key"] = ""
    elif patch.llm_api_key is not None and patch.llm_api_key.strip():
        data["llm_api_key"] = patch.llm_api_key.strip()

    if patch.clear_cloud_asr_api_key:
        data["cloud_asr_api_key"] = ""
    elif patch.cloud_asr_api_key is not None and patch.cloud_asr_api_key.strip():
        data["cloud_asr_api_key"] = patch.cloud_asr_api_key.strip()

    runtime = RuntimeSettings(**data)
    runtime = save_runtime(db, runtime)
    upsert_llm_profile(db, runtime)
    return runtime


def upsert_llm_profile(db: Session, runtime: RuntimeSettings, *, name: str = "") -> LLMProfile | None:
    """Keep a visible list of saved API + model combinations (never store blank keys)."""
    if not runtime.llm_api_key or not runtime.llm_base_url or not runtime.llm_model:
        return None
    profile = (
        db.query(LLMProfile)
        .filter(
            LLMProfile.base_url == runtime.llm_base_url,
            LLMProfile.model == runtime.llm_model,
        )
        .one_or_none()
    )
    if profile is None:
        host = urlparse(runtime.llm_base_url).netloc or runtime.llm_base_url
        profile = LLMProfile(
            name=name or f"{runtime.llm_model} · {host}",
            base_url=runtime.llm_base_url,
            model=runtime.llm_model,
            api_key=runtime.llm_api_key,
            temperature=runtime.llm_temperature,
        )
        db.add(profile)
    else:
        profile.api_key = runtime.llm_api_key
        profile.temperature = runtime.llm_temperature
        if name:
            profile.name = name
    db.commit()
    db.refresh(profile)
    return profile
