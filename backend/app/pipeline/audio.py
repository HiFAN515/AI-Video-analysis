"""Audio extraction, source separation, denoising and loudness normalisation."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np

from .control import TaskCancelled, TaskPaused

ProgressFn = Callable[[float, str], None]


class AudioError(RuntimeError):
    pass


def _run(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AudioError(f"未找到命令：{cmd[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise AudioError(detail[-1] if detail else f"命令执行失败：{cmd[0]}") from exc


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def probe_duration(path: Path) -> float:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    data = json.loads(result.stdout.decode("utf-8", "ignore"))
    try:
        return float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def extract_wav(src: Path, dst: Path, *, sample_rate: int = 44100, channels: int = 2) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-vn",
            "-ac",
            str(channels),
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(dst),
        ]
    )
    if not dst.exists() or dst.stat().st_size == 0:
        raise AudioError("音轨提取失败，输出文件为空")
    return dst


def convert_audio(
    src: Path,
    dst: Path,
    *,
    sample_rate: int | None = None,
    channels: int | None = None,
    loudnorm: bool = False,
    bitrate: str = "128k",
) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn"]
    if channels:
        cmd += ["-ac", str(channels)]
    if sample_rate:
        cmd += ["-ar", str(sample_rate)]
    filters = []
    if loudnorm:
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if filters:
        cmd += ["-af", ",".join(filters)]
    if dst.suffix.lower() in {".mp3", ".m4a", ".aac", ".ogg", ".opus"}:
        cmd += ["-b:a", bitrate]
    cmd.append(str(dst))
    _run(cmd)
    return dst


def set_torch_threads(threads: int) -> None:
    try:
        import torch

        torch.set_num_threads(max(1, threads))
    except Exception:
        pass


_DEMUCS_BOOTSTRAPPED = False


def _bootstrap_demucs() -> None:
    """Make Demucs importable without a working torchaudio build.

    Demucs imports ``openunmix`` for Wiener filtering, and openunmix imports
    torchaudio at module level even though htdemucs (``wiener_iters=0``) never
    calls it. When torchaudio is missing or built against another torch ABI we
    register a tiny placeholder module so the import chain succeeds. All audio
    I/O in this project goes through ffmpeg + soundfile instead.
    """
    global _DEMUCS_BOOTSTRAPPED
    if _DEMUCS_BOOTSTRAPPED:
        return
    try:
        import torchaudio  # noqa: F401
    except Exception:
        import sys
        import types

        shim = types.ModuleType("torchaudio")

        def _unsupported(*_args, **_kwargs):
            raise AudioError("torchaudio 不可用；本项目使用 ffmpeg + soundfile 处理音频")

        shim.load = _unsupported
        shim.save = _unsupported
        shim.info = _unsupported
        shim.get_audio_backend = lambda: None
        shim.set_audio_backend = lambda *args, **kwargs: None
        shim.__version__ = "0.0.0+stub"
        sys.modules["torchaudio"] = shim
    _DEMUCS_BOOTSTRAPPED = True


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None and importlib.util.find_spec("torch") is not None


def _crossfade(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Linear crossfade between the tail of one chunk and the head of the next."""
    n = min(a.shape[-1], b.shape[-1])
    if n <= 0:
        return b[..., :0]
    fade = np.linspace(0.0, 1.0, n, dtype=np.float32)
    return a[..., :n] * (1.0 - fade) + b[..., :n] * fade


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def resolve_torch_device(requested: str) -> str:
    requested = (requested or "auto").lower()
    if requested == "cuda":
        return "cuda" if cuda_available() else "cpu"
    if requested == "auto":
        return "cuda" if cuda_available() else "cpu"
    return "cpu"


def gpu_present() -> bool:
    """True when an NVIDIA driver is loaded, even if this process cannot open the device."""
    from pathlib import Path as _Path

    if _Path("/proc/driver/nvidia/version").exists():
        return True
    return shutil.which("nvidia-smi") is not None


def gpu_name() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_name(0)
    except Exception:
        pass
    return ""


def _chunked_process(
    src: Path,
    dst: Path,
    *,
    sample_rate: int,
    channels: int,
    chunk_seconds: float,
    overlap_seconds: float,
    process: Callable[[np.ndarray, int], np.ndarray],
    on_progress: ProgressFn | None,
    label: str,
    control: Callable[[], None] | None = None,
) -> Path:
    """Stream a wav file through ``process`` chunk by chunk with overlap crossfades.

    ``process`` accepts a float32 array of shape (channels, frames) and returns the
    same shape. Avoids loading hour-long audio into memory.
    """
    import soundfile as sf

    dst.parent.mkdir(parents=True, exist_ok=True)
    chunk = max(1, int(chunk_seconds * sample_rate))
    overlap = max(1, int(overlap_seconds * sample_rate))
    total = max(1, sf.info(str(src)).frames)

    with sf.SoundFile(str(src)) as fin, sf.SoundFile(
        str(dst), "w", samplerate=sample_rate, channels=channels, subtype="PCM_16"
    ) as fout:
        if fin.samplerate != sample_rate or fin.channels != channels:
            raise AudioError(f"音频格式不符：expect {sample_rate}Hz/{channels}ch")
        start = 0
        tail: np.ndarray | None = None
        first = True
        while start < total:
            fin.seek(start)
            frames = min(chunk + overlap, total - start)
            data = fin.read(frames=frames, dtype="float32", always_2d=True)
            if data.size == 0:
                break
            audio = np.ascontiguousarray(data.T)
            out = process(audio, sample_rate)
            out = np.asarray(out, dtype=np.float32)
            if out.ndim == 1:
                out = out[None, :]

            if first:
                write = out[:, :chunk] if out.shape[1] > chunk else out
                fout.write(write.T)
                tail = out[:, chunk:]
                first = False
            else:
                assert tail is not None
                blended = _crossfade(tail, out)
                fout.write(blended.T)
                rest = out[:, blended.shape[1] :]
                if rest.shape[1] > overlap:
                    fout.write(rest[:, : rest.shape[1] - overlap].T)
                    tail = rest[:, -overlap:]
                else:
                    tail = rest
            start += chunk
            if control:
                control()
            if on_progress:
                on_progress(min(0.99, start / total), label)

        if tail is not None and tail.shape[1] > 0:
            fout.write(tail.T)

    if on_progress:
        on_progress(1.0, label)
    return dst


def _separate_vocals_impl(
    src: Path,
    dst: Path,
    *,
    device: str,
    threads: int,
    on_progress: ProgressFn | None,
    control: Callable[[], None] | None,
) -> Path:
    import soundfile as sf
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    if device == "cpu":
        set_torch_threads(threads)
    info = sf.info(str(src))
    device_label = "CUDA" if device == "cuda" else "CPU"
    if on_progress:
        on_progress(0.02, f"加载人声分离模型 htdemucs（{device_label}）")

    try:
        model = get_model("htdemucs")
        model.eval()
        model.to(device)
    except Exception as exc:  # pragma: no cover - depends on external weights
        raise AudioError(f"人声分离模型加载失败：{exc}") from exc

    if info.samplerate != model.samplerate:
        raise AudioError(f"人声分离需要 {model.samplerate}Hz 音频")

    total = max(1, info.frames)
    chunk = int(60 * model.samplerate)  # 1 minute per pass keeps VRAM low and pause responsive
    overlap = int(1.0 * model.samplerate)

    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sf.SoundFile(str(src)) as fin, sf.SoundFile(
            str(dst), "w", samplerate=model.samplerate, channels=2, subtype="PCM_16"
        ) as fout:
            start = 0
            tail: np.ndarray | None = None
            first = True
            while start < total:
                fin.seek(start)
                frames = min(chunk + overlap, total - start)
                data = fin.read(frames=frames, dtype="float32", always_2d=True)
                if data.size == 0:
                    break
                wav = torch.from_numpy(np.ascontiguousarray(data.T)).to(device)
                ref = wav.mean(0)
                norm = (wav - ref.mean()) / (ref.std() + 1e-8)
                with torch.no_grad():
                    sources = apply_model(
                        model,
                        norm[None],
                        device=device,
                        shifts=1,
                        split=True,
                        overlap=0.25,
                        progress=False,
                    )[0]
                sources = sources * ref.std() + ref.mean()
                voc = sources[model.sources.index("vocals")].detach().cpu().numpy().astype(np.float32)

                if first:
                    write = voc[:, :chunk] if voc.shape[1] > chunk else voc
                    fout.write(write.T)
                    tail = voc[:, chunk:]
                    first = False
                else:
                    assert tail is not None
                    blended = _crossfade(tail, voc)
                    fout.write(blended.T)
                    rest = voc[:, blended.shape[1] :]
                    if rest.shape[1] > overlap:
                        fout.write(rest[:, : rest.shape[1] - overlap].T)
                        tail = rest[:, -overlap:]
                    else:
                        tail = rest

                start += chunk
                if control:
                    control()
                if on_progress:
                    pct = min(0.98, start / total)
                    on_progress(pct, f"分离人声 {int(pct * 100)}%（{device_label}）")

            if tail is not None and tail.shape[1] > 0:
                fout.write(tail.T)
    finally:
        if device == "cuda":
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass

    if on_progress:
        on_progress(1.0, f"人声分离完成（{device_label}）")
    return dst


def separate_vocals(
    src: Path,
    dst: Path,
    *,
    device: str = "auto",
    threads: int = 8,
    on_progress: ProgressFn | None = None,
    control: Callable[[], None] | None = None,
) -> Path:
    """Remove background music / effects using Demucs htdemucs and keep the vocal stem."""
    if not demucs_available():
        raise AudioError("未安装 Demucs，无法分离背景音乐")

    _bootstrap_demucs()
    resolved = resolve_torch_device(device)
    try:
        return _separate_vocals_impl(
            src, dst, device=resolved, threads=threads, on_progress=on_progress, control=control
        )
    except (TaskPaused, TaskCancelled):
        raise
    except Exception as exc:
        if resolved != "cuda":
            raise
        # 8GB GPUs can still OOM on very long chunks; retry on CPU instead of failing the task.
        dst.unlink(missing_ok=True)
        if on_progress:
            on_progress(0.02, f"GPU 人声分离失败，回退 CPU：{exc}")
        return _separate_vocals_impl(
            src, dst, device="cpu", threads=threads, on_progress=on_progress, control=control
        )


def denoise(
    src: Path,
    dst: Path,
    *,
    strength: float = 0.85,
    on_progress: ProgressFn | None = None,
    control: Callable[[], None] | None = None,
) -> Path:
    """Spectral-gating noise reduction, streamed in chunks."""
    import noisereduce as nr
    import soundfile as sf

    info = sf.info(str(src))
    if info.channels != 1:
        raise AudioError("降噪只支持单声道输入")

    strength = float(np.clip(strength, 0.0, 1.0))

    def process(audio: np.ndarray, sr: int) -> np.ndarray:
        y = audio[0]
        reduced = nr.reduce_noise(
            y=y,
            sr=sr,
            stationary=False,
            prop_decrease=strength,
            n_fft=1024,
            win_length=1024,
            hop_length=256,
        )
        return reduced[None, :]

    return _chunked_process(
        src,
        dst,
        sample_rate=info.samplerate,
        channels=1,
        chunk_seconds=300,
        overlap_seconds=0.5,
        process=process,
        on_progress=on_progress,
        control=control,
        label="降噪处理",
    )


def loudnorm(src: Path, dst: Path, *, sample_rate: int = 16000) -> Path:
    return convert_audio(src, dst, sample_rate=sample_rate, channels=1, loudnorm=True)


def make_playback_mp3(src: Path, dst: Path) -> Path:
    return convert_audio(src, dst, loudnorm=True, bitrate="160k")


def wav_to_16k_mono(src: Path, dst: Path) -> Path:
    return convert_audio(src, dst, sample_rate=16000, channels=1)


def decode_file(src: Path, *, sample_rate: int, channels: int) -> np.ndarray:
    """Read a wav into a (channels, frames) float32 array."""
    import soundfile as sf

    data, sr = sf.read(str(src), dtype="float32", always_2d=True)
    if sr != sample_rate or data.shape[1] != channels:
        raise AudioError(f"音频格式不符：{sr}Hz/{data.shape[1]}ch")
    return np.ascontiguousarray(data.T)


def cleanup_intermediates(work_dir: Path, keep: set[str]) -> None:
    """Delete bulky intermediate wavs but keep selected file names."""
    if not work_dir.exists():
        return
    for path in work_dir.iterdir():
        if path.is_file() and path.name not in keep and path.suffix.lower() in {
            ".wav",
            ".part",
            ".ytdl",
        }:
            try:
                path.unlink()
            except OSError:
                pass
