"""Speaker diarization using sherpa-onnx (offline, no HuggingFace token required)."""
from __future__ import annotations

import os
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from ..config import settings

ProgressFn = Callable[[float, str], None]

SEGMENTATION_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMBEDDING_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/"
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)

SEGMENTATION_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
SEGMENTATION_MODEL = "model.onnx"
EMBEDDING_MODEL = "3dspeaker_eres2net_zh_cn_16k.onnx"


@dataclass
class Turn:
    start: float
    end: float
    speaker: int


class DiarizationError(RuntimeError):
    pass


def segmentation_path() -> Path:
    return settings.diarization_dir / SEGMENTATION_DIR / SEGMENTATION_MODEL


def embedding_path() -> Path:
    return settings.diarization_dir / EMBEDDING_MODEL


def models_ready() -> bool:
    return segmentation_path().exists() and embedding_path().exists()


def ensure_models(on_progress: ProgressFn | None = None) -> None:
    """Download the two small ONNX models on first use (~40MB total)."""
    settings.diarization_dir.mkdir(parents=True, exist_ok=True)

    if not segmentation_path().exists():
        archive = settings.diarization_dir / "segmentation.tar.bz2"
        _download(SEGMENTATION_URL, archive, on_progress, "下载说话人分割模型")
        with tarfile.open(archive, "r:bz2") as tar:
            tar.extractall(settings.diarization_dir)
        archive.unlink(missing_ok=True)

    if not embedding_path().exists():
        _download(EMBEDDING_URL, embedding_path(), on_progress, "下载说话人声纹模型")

    if not models_ready():
        raise DiarizationError("说话人分离模型下载不完整，请重试")


def _download(
    url: str,
    dest: Path,
    on_progress: ProgressFn | None,
    label: str,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        proxy = settings.proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            proxy=proxy or None,
            trust_env=False,
            timeout=httpx.Timeout(connect=20.0, read=120.0, write=20.0, pool=20.0),
        ) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            done = 0
            with tmp.open("wb") as handle:
                for chunk in response.iter_bytes(chunk_size=1024 * 256):
                    handle.write(chunk)
                    done += len(chunk)
                    if on_progress and total:
                        on_progress(min(0.99, done / total), f"{label} {int(done / total * 100)}%")
        tmp.replace(dest)
        if on_progress:
            on_progress(1.0, f"{label}完成")
    except httpx.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise DiarizationError(f"{label}失败：{exc}") from exc


def diarize(
    wav_path: Path,
    *,
    max_speakers: int = 0,
    threshold: float = 0.5,
    threads: int = 4,
    on_progress: ProgressFn | None = None,
) -> list[Turn]:
    """Return speaker turns for a 16kHz mono wav file."""
    if not models_ready():
        ensure_models(on_progress)

    import soundfile as sf
    import sherpa_onnx

    ensure_models(on_progress)
    samples, sample_rate = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if sample_rate != 16000 or samples.ndim != 1:
        raise DiarizationError("说话人分离需要 16kHz 单声道音频")

    segmentation = sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
            model=str(segmentation_path())
        ),
        num_threads=threads,
        debug=False,
        provider="cpu",
    )
    embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=str(embedding_path()),
        num_threads=threads,
        debug=False,
        provider="cpu",
    )
    if max_speakers and max_speakers > 0:
        clustering = sherpa_onnx.FastClusteringConfig(
            num_clusters=max_speakers, threshold=threshold
        )
    else:
        clustering = sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=threshold)

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=segmentation,
        embedding=embedding,
        clustering=clustering,
        min_duration_on=0.25,
        min_duration_off=0.35,
    )
    recognizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    if hasattr(recognizer, "validate") and not recognizer.validate():
        raise DiarizationError("说话人分离配置校验失败，请检查模型文件")

    def callback(processed: int, total: int) -> int:
        if on_progress and total:
            on_progress(min(0.99, processed / total), f"说话人分离 {int(processed / total * 100)}%")
        return 0

    try:
        result = recognizer.process(samples, callback=callback)
        # sherpa-onnx returns a result object; older versions returned a list.
        segments = result.sort_by_start_time() if hasattr(result, "sort_by_start_time") else list(result)
    except Exception as exc:  # pragma: no cover - runtime native errors
        raise DiarizationError(f"说话人分离失败：{exc}") from exc

    turns = [Turn(start=float(s.start), end=float(s.end), speaker=int(s.speaker)) for s in segments]
    turns.sort(key=lambda t: (t.start, t.end))
    if on_progress:
        on_progress(1.0, f"检测到 {len({t.speaker for t in turns})} 位说话人")

    if not turns:
        return []
    return _fill_gaps(turns, duration=len(samples) / sample_rate)


def _fill_gaps(turns: list[Turn], duration: float, max_gap: float = 1.0) -> list[Turn]:
    """Fill tiny silences so short interjections get a speaker instead of being orphaned."""
    if not turns:
        return turns
    merged: list[Turn] = [turns[0]]
    for turn in turns[1:]:
        prev = merged[-1]
        if turn.start - prev.end <= max_gap and turn.speaker == prev.speaker:
            prev.end = max(prev.end, turn.end)
        else:
            merged.append(turn)
    if merged[0].start > 0:
        merged[0].start = 0.0
    if merged[-1].end < duration:
        merged[-1].end = duration
    return merged
