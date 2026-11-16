"""Speech recognition backends: local faster-whisper and OpenAI-compatible cloud ASR."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx

from ..config import settings
from .control import TaskCancelled, TaskPaused

ProgressFn = Callable[[float, str], None]


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class RawSegment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class ASRResult:
    segments: list[RawSegment]
    language: str
    model: str
    backend: str


class ASRError(RuntimeError):
    pass


def available_ram_gb() -> float:
    """Best-effort available memory, used to avoid OOM-ing the desktop."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 999.0


def cuda_vram_gb() -> float:
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / 1024**3
    except Exception:
        pass
    return 0.0


def guard_local_model(model_name: str, device: str) -> tuple[str, str | None]:
    """Downgrade oversized models on low-memory CPU machines.

    Loading large-v3 on CPU costs several GB of RAM; on a low-memory laptop running a
    browser and the rest of the pipeline this can freeze the whole desktop.
    """
    name = (model_name or "small").lower()
    if device == "cuda":
        vram = cuda_vram_gb()
        if "large" in name and 0 < vram < 12:
            target = "medium" if vram >= 7 else "small"
            return target, (
                f"显存约 {vram:.1f}GB，large 模型容易 OOM，已自动改用 {target} "
                "（8GB 显存建议 small / medium）"
            )
        if "medium" in name and 0 < vram < 5:
            return "small", f"显存约 {vram:.1f}GB，已自动改用 small"
        return model_name, None

    available = available_ram_gb()
    if "large" in name and available < 10.0:
        return "small", (
            f"当前可用内存约 {available:.1f}GB，低于 large 模型建议的 10GB，"
            "已自动改用 small 以避免系统卡死"
        )
    if "medium" in name and available < 5.0:
        return "small", (
            f"当前可用内存约 {available:.1f}GB，低于 medium 模型建议的 5GB，已自动改用 small"
        )
    return model_name, None


def resolve_local_model(name: str) -> str:
    """Resolve a model name/path, preferring an explicitly downloaded local copy."""
    cleaned = (name or "small").strip()
    path = Path(cleaned)
    if path.exists():
        return str(path)
    local = settings.model_dir / "asr" / cleaned
    if local.exists():
        return str(local)
    # faster-whisper understands "small", "large-v3", "Systran/faster-whisper-large-v3"
    return cleaned


def local_model_ready(name: str) -> bool:
    path = Path(resolve_local_model(name))
    if path.is_dir():
        return any(path.glob("model.bin")) or any(path.glob("*.bin"))
    return False


def _is_chinese(language: str | None) -> bool:
    return bool(language) and language.lower().startswith("zh")


def torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def transcribe_local(
    wav_path: Path,
    *,
    model_name: str = "small",
    language: str = "auto",
    threads: int = 8,
    compute_type: str = "int8",
    device: str = "cpu",
    initial_prompt: str | None = None,
    hotwords: str | None = None,
    on_progress: ProgressFn | None = None,
    control: Callable[[], None] | None = None,
) -> ASRResult:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise ASRError("未安装 faster-whisper") from exc

    requested_device = device if device in {"cpu", "cuda", "auto"} else "cpu"
    if requested_device == "cuda" and not torch_cuda_available():
        requested_device = "cpu"
        compute_type = "int8"
        if on_progress:
            on_progress(0.005, "当前进程无法访问 CUDA，语音识别改用 CPU int8")

    if requested_device == "cuda":
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() < 1:
                raise RuntimeError("CTranslate2 未检测到 CUDA 设备")
        except Exception as exc:
            requested_device = "cpu"
            compute_type = "int8"
            if on_progress:
                on_progress(
                    0.005,
                    f"CTranslate2 CUDA 不可用（{exc}），回退 CPU int8；"
                    "faster-whisper 需要 CUDA 12 的 libcublas.so.12（CUDA 13 的 .so.13 不兼容）",
                )

    model_name, guard_warning = guard_local_model(model_name, requested_device)
    if guard_warning and on_progress:
        on_progress(0.005, guard_warning)

    ready = local_model_ready(model_name)
    if on_progress:
        on_progress(
            0.01,
            f"加载语音识别模型 {model_name}" if ready else "加载语音识别模型（首次使用需要下载模型）",
        )

    resolved = resolve_local_model(model_name)
    try:
        model = WhisperModel(
            resolved,
            device=requested_device,
            compute_type=compute_type,
            cpu_threads=max(1, threads),
            num_workers=1,
            download_root=str(settings.model_dir / "asr"),
        )
    except Exception as exc:
        if requested_device == "cuda":
            # Never fall back to a CPU large model on a low-memory machine.
            fallback_name, fallback_warning = guard_local_model(model_name, "cpu")
            resolved = resolve_local_model(fallback_name)
            if on_progress:
                note = f"（{fallback_warning}）" if fallback_warning else ""
                on_progress(0.01, f"CUDA 不可用（{exc}），回退到 CPU int8 {fallback_name}{note}")
            try:
                model = WhisperModel(
                    resolved,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=max(1, threads),
                    num_workers=1,
                    download_root=str(settings.model_dir / "asr"),
                )
            except Exception as inner:
                raise ASRError(f"语音识别模型加载失败：{inner}") from inner
        else:
            raise ASRError(f"语音识别模型加载失败：{exc}") from exc

    lang = None if not language or language == "auto" else language
    prompt = None
    if _is_chinese(lang):
        prompt = "以下是普通话的句子，请使用简体中文并加上标点。"
        if initial_prompt:
            prompt = f"视频主题与专有名词：{initial_prompt[:200]}。{prompt}"
    elif initial_prompt:
        prompt = f"Video topic and proper nouns: {initial_prompt[:200]}"

    kwargs: dict[str, Any] = {
        "language": lang,
        "beam_size": 5,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 400, "speech_pad_ms": 200},
        "word_timestamps": True,
        "condition_on_previous_text": False,
        "no_speech_threshold": 0.6,
        "compression_ratio_threshold": 2.4,
        "initial_prompt": prompt,
    }
    if hotwords and _is_chinese(lang):
        kwargs["hotwords"] = hotwords[:120]

    try:
        segments_iter, info = model.transcribe(str(wav_path), **kwargs)
    except Exception as exc:
        # Older / unusual builds may not ship the Silero VAD asset.
        kwargs["vad_filter"] = False
        try:
            segments_iter, info = model.transcribe(str(wav_path), **kwargs)
        except Exception as inner:
            raise ASRError(f"语音识别失败：{inner or exc}") from inner

    detected = getattr(info, "language", None) or (lang or "")
    duration = float(getattr(info, "duration", 0) or 0)
    if duration <= 0:
        try:
            from .audio import probe_duration

            duration = probe_duration(wav_path)
        except Exception:
            duration = 0

    raw: list[RawSegment] = []
    last_report = -1.0

    def consume(iterator) -> None:
        nonlocal last_report
        for seg in iterator:
            if control:
                control()
            text = (seg.text or "").strip()
            if not text:
                continue
            words = [
                Word(start=float(w.start), end=float(w.end), text=w.word)
                for w in (seg.words or [])
                if w.word and w.word.strip()
            ]
            raw.append(RawSegment(start=float(seg.start), end=float(seg.end), text=text, words=words))
            if on_progress and duration > 0:
                frac = min(0.99, max(0.0, float(seg.end) / duration))
                if frac - last_report >= 0.02:
                    last_report = frac
                    on_progress(frac, f"识别中 {int(frac * 100)}%")

    try:
        consume(segments_iter)
    except (TaskPaused, TaskCancelled):
        raise
    except Exception as exc:
        # CTranslate2 can defer CUDA initialization until the first segment, where
        # missing libcublas/cuDNN surfaces. Retry once on CPU instead of failing.
        if requested_device != "cuda":
            raise ASRError(f"语音识别失败：{exc}") from exc
        fallback_name, fallback_warning = guard_local_model(model_name, "cpu")
        resolved = resolve_local_model(fallback_name)
        if on_progress:
            note = f"（{fallback_warning}）" if fallback_warning else ""
            on_progress(0.01, f"CUDA 识别失败（{exc}），回退 CPU int8 {fallback_name}{note}")
        model = WhisperModel(
            resolved,
            device="cpu",
            compute_type="int8",
            cpu_threads=max(1, threads),
            num_workers=1,
            download_root=str(settings.model_dir / "asr"),
        )
        raw = []
        last_report = -1.0
        segments_iter, info = model.transcribe(str(wav_path), **kwargs)
        consume(segments_iter)

    if not raw:
        raise ASRError("没有识别到语音内容，请确认视频包含人声")

    if on_progress:
        on_progress(1.0, f"识别完成，共 {len(raw)} 段")
    return ASRResult(segments=raw, language=detected, model=resolved, backend="local")


def prepare_cloud_audio(src_wav: Path, dst_mp3: Path) -> Path:
    from .audio import convert_audio

    return convert_audio(src_wav, dst_mp3, sample_rate=16000, channels=1, bitrate="48k")


def transcribe_cloud(
    audio_path: Path,
    *,
    base_url: str,
    api_key: str,
    model: str,
    language: str = "auto",
    prompt: str | None = None,
    on_progress: ProgressFn | None = None,
    control: Callable[[], None] | None = None,
) -> ASRResult:
    if not api_key:
        raise ASRError("云端语音识别未配置 API Key")
    endpoint = base_url.rstrip("/") + "/audio/transcriptions"
    data: dict[str, str] = {"model": model, "response_format": "verbose_json"}
    if language and language != "auto":
        data["language"] = language
    if prompt:
        data["prompt"] = prompt[:400]

    if control:
        control()
    if on_progress:
        on_progress(0.05, "上传音频到云端识别服务")

    try:
        with audio_path.open("rb") as handle:
            files = {"file": (audio_path.name, handle, "audio/mpeg")}
            with httpx.Client(
                timeout=httpx.Timeout(connect=20.0, read=1800.0, write=300.0, pool=30.0),
                proxy=settings.proxy or None,
                trust_env=False,
            ) as client:
                response = client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                )
    except httpx.HTTPError as exc:
        raise ASRError(f"云端语音识别请求失败：{exc}") from exc

    if response.status_code >= 400:
        raise ASRError(_cloud_error(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise ASRError("云端语音识别返回了无法解析的内容") from exc

    raw: list[RawSegment] = []
    for item in payload.get("segments") or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        raw.append(
            RawSegment(
                start=float(item.get("start") or 0),
                end=float(item.get("end") or 0),
                text=text,
            )
        )

    if not raw:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise ASRError("云端语音识别没有返回文字")
        from .audio import probe_duration

        raw = [RawSegment(start=0.0, end=probe_duration(audio_path), text=text)]

    if on_progress:
        on_progress(1.0, f"云端识别完成，共 {len(raw)} 段")
    return ASRResult(
        segments=raw,
        language=str(payload.get("language") or language or ""),
        model=model,
        backend="cloud",
    )


def _cloud_error(response: httpx.Response) -> str:
    detail = ""
    try:
        payload = response.json()
        detail = str(payload.get("error", {}).get("message") or payload.get("message") or "")
    except Exception:
        detail = response.text[:300]
    mapping = {
        401: "API Key 无效或已过期",
        403: "没有访问该识别服务的权限",
        413: "音频文件超过服务商大小限制",
        429: "请求过于频繁，请稍后重试",
    }
    base = mapping.get(response.status_code, f"云端识别失败（HTTP {response.status_code}）")
    return f"{base}：{detail}" if detail else base


def iter_text_chunks(text: str, size: int = 2000) -> Iterator[str]:
    """Split long text on paragraph boundaries (used for context building)."""
    if len(text) <= size:
        yield text
        return
    buffer: list[str] = []
    length = 0
    for line in text.splitlines(keepends=True):
        if length + len(line) > size and buffer:
            yield "".join(buffer)
            buffer, length = [], 0
        buffer.append(line)
        length += len(line)
    if buffer:
        yield "".join(buffer)


def cpu_count() -> int:
    return settings.cpu_threads or max(1, os.cpu_count() or 1)
