"""Application configuration.

Environment variables use the ``VM_`` prefix (e.g. ``VM_LLM_API_KEY``).
Runtime-editable settings live in SQLite and are merged on top of these defaults.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

# Model caches must live inside the project: ~/.cache is often read-only in
# sandboxed / containerised environments and we want models to travel with the app.
os.environ.setdefault("HF_HOME", str(ROOT_DIR / ".cache" / "hf"))
os.environ.setdefault("TORCH_HOME", str(ROOT_DIR / ".cache" / "torch"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT_DIR / ".cache"))
# Xet downloads can stall behind some proxies; regular HTTP is slower but reliable.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# Mainland China friendly mirror; override with HF_ENDPOINT=https://huggingface.co
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# httpx (and some SDKs) refuse a socks:// URL unless the socks extra is installed.
# Prefer the plain HTTP proxy variables, which every download path understands.
for _key in ("ALL_PROXY", "all_proxy"):
    if os.environ.get(_key, "").lower().startswith("socks"):
        os.environ.pop(_key, None)

# httpx chokes on bracketed IPv6 entries inside NO_PROXY (e.g. "[::1]").
for _key in ("NO_PROXY", "no_proxy"):
    _value = os.environ.get(_key)
    if _value:
        _parts = [part.strip() for part in _value.split(",") if part.strip()]
        _safe = [part for part in _parts if ":" not in part]
        os.environ[_key] = ",".join(_safe)


def _default_proxy() -> str:
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VM_",
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- paths -------------------------------------------------------------
    data_dir: Path = ROOT_DIR / "data"
    model_dir: Path = ROOT_DIR / "models"
    frontend_dist: Path = ROOT_DIR / "frontend" / "dist"

    # ---- server ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 7860
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # ---- pipeline ----------------------------------------------------------
    max_concurrent_jobs: int = 1
    cpu_threads: int = min(16, os.cpu_count() or 4)
    proxy: str = _default_proxy()
    cookies_file: str = ""

    # ---- LLM (OpenAI compatible) ------------------------------------------
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.3

    # ---- ASR ---------------------------------------------------------------
    asr_backend: str = "local"  # local | cloud
    asr_model: str = "small"
    asr_device: str = "cpu"
    asr_compute_type: str = "int8"
    asr_language: str = "auto"
    cloud_asr_base_url: str = "https://api.openai.com/v1"
    cloud_asr_api_key: str = ""
    cloud_asr_model: str = "whisper-1"

    # ---- audio defaults ----------------------------------------------------
    audio_device: str = "auto"  # auto | cpu | cuda (Demucs)
    bgm_separation: bool = True
    denoise: bool = True
    diarization_enabled: bool = True
    diarization_max_speakers: int = 0
    diarization_threshold: float = 0.5
    default_delete_audio: bool = False

    @field_validator("data_dir", "model_dir", "frontend_dist", mode="after")
    @classmethod
    def _project_relative(cls, value: Path) -> Path:
        if value.is_absolute():
            return value
        return (ROOT_DIR / value).resolve()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def diarization_dir(self) -> Path:
        return self.model_dir / "diarization"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.audio_dir,
            self.model_dir / "asr",
            self.diarization_dir,
            self.data_dir / "logs",
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()

# Make the runtime proxy (e.g. from .env) visible to HuggingFace / torch downloads too.
if settings.proxy:
    os.environ.setdefault("HTTPS_PROXY", settings.proxy)
    os.environ.setdefault("HTTP_PROXY", settings.proxy)
