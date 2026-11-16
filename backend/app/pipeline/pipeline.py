"""The end-to-end audio -> transcript pipeline."""
from __future__ import annotations

import gc
import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import joinedload

from ..config import settings
from ..db import session_scope
from ..models import Segment, Task, TaskEvent, Transcript, Video, utcnow
from ..services.settings_store import RuntimeSettings, get_runtime
from . import asr, audio, diarization, downloader
from .control import TaskCancelled, TaskPaused
from .transcript import FinalSegment, build_segments, render_text

STAGE_ORDER = [
    "download",
    "extract",
    "separate",
    "denoise",
    "normalize",
    "diarize",
    "transcribe",
    "finalize",
]
STAGE_INDEX = {name: index for index, name in enumerate(STAGE_ORDER)}

STAGE_LABELS = {
    "queued": "排队中",
    "prepare": "准备",
    "download": "获取音频",
    "extract": "提取音轨",
    "separate": "分离人声",
    "denoise": "降噪",
    "normalize": "响度标准化",
    "diarize": "说话人分离",
    "transcribe": "语音识别",
    "finalize": "生成文稿",
    "paused": "已暂停",
    "done": "完成",
    "canceled": "已取消",
}


class PipelineError(RuntimeError):
    pass


class Control:
    """Reads pause/cancel requests without hammering SQLite on every ASR segment."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._last_check = 0.0

    def check(self, stage: str, *, next_stage: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_check < 0.6:
            return
        self._last_check = now
        with session_scope() as db:
            task = db.get(Task, self.task_id)
            if task is None or task.status == "canceled" or task.cancel_requested:
                raise TaskCancelled()
            if task.pause_requested:
                raise TaskPaused(next_stage or stage)


@dataclass
class JobContext:
    task_id: str
    url: str
    title: str
    uploader: str
    description: str
    duration: float
    options: dict[str, Any]
    runtime: RuntimeSettings
    workdir: Path
    delete_after: bool
    asr_model: str
    asr_backend: str
    resume_stage: str = ""
    reported_progress: int = -1
    last_message: str = ""
    current_stage: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _load_job(task_id: str) -> JobContext | None:
    with session_scope() as db:
        task = (
            db.query(Task)
            .options(joinedload(Task.video))
            .filter(Task.id == task_id)
            .one_or_none()
        )
        if task is None:
            return None
        runtime = get_runtime(db)
        options = dict(task.options or {})
        video: Video = task.video
        return JobContext(
            task_id=task.id,
            url=video.url,
            title=video.title,
            uploader=video.uploader,
            description=video.description,
            duration=video.duration_sec,
            options=options,
            runtime=runtime,
            workdir=settings.audio_dir / task.id,
            delete_after=bool(options.get("delete_audio")),
            asr_model=str(options.get("asr_model") or runtime.asr_model),
            asr_backend=str(options.get("asr_backend") or runtime.asr_backend),
            resume_stage=task.resume_stage or "",
        )


def _update_task(task_id: str, **fields: Any) -> None:
    with session_scope() as db:
        task = db.get(Task, task_id)
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)


def _log(task_id: str, stage: str, message: str, level: str = "info", progress: int | None = None) -> None:
    with session_scope() as db:
        db.add(
            TaskEvent(
                task_id=task_id,
                stage=stage,
                level=level,
                message=message[:4000],
                progress=progress,
            )
        )
        count = db.query(TaskEvent).filter(TaskEvent.task_id == task_id).count()
        if count > 400:
            for event in (
                db.query(TaskEvent)
                .filter(TaskEvent.task_id == task_id)
                .order_by(TaskEvent.id)
                .limit(count - 400)
                .all()
            ):
                db.delete(event)


def _is_cancelled(task_id: str) -> bool:
    with session_scope() as db:
        task = db.get(Task, task_id)
        return bool(task and (task.cancel_requested or task.status == "canceled"))


class Pipeline:
    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx
        self.task_id = ctx.task_id
        self.stage = "prepare"
        self.control = Control(ctx.task_id)

    # -- progress ---------------------------------------------------------
    def report(self, progress: float, message: str = "", *, stage: str | None = None, log: bool = False) -> None:
        if stage and stage != self.stage:
            self.stage = stage
            self.ctx.reported_progress = -1
            self.ctx.last_message = ""
            log = True
        value = int(max(0, min(99, progress)))
        changed = value != self.ctx.reported_progress
        message_changed = message != self.ctx.last_message
        if changed or (message_changed and log):
            self.ctx.reported_progress = value
            self.ctx.last_message = message
            _update_task(self.task_id, stage=self.stage, progress=value, message=message[:500])
        if log:
            _log(self.task_id, self.stage, message or STAGE_LABELS.get(self.stage, self.stage), progress=value)

    def stage_progress(self, start: float, end: float) -> Callable[[float, str], None]:
        def callback(frac: float, message: str) -> None:
            self.report(start + (end - start) * max(0.0, min(1.0, frac)), message)

        return callback

    def checkpoint(self, next_stage: str) -> None:
        self.control.check(self.stage, next_stage=next_stage, force=True)

    # -- main -------------------------------------------------------------
    def run(self) -> None:
        ctx = self.ctx
        ctx.workdir.mkdir(parents=True, exist_ok=True)

        with session_scope() as db:
            existing = db.get(Task, self.task_id)
            if existing is None:
                return
            current_status = existing.status
            pre_pause = bool(existing.pause_requested)
            pre_resume = existing.resume_stage or ""

        if current_status == "paused":
            return
        if pre_pause:
            _update_task(
                self.task_id,
                status="paused",
                stage=existing.stage or "queued",
                message="已暂停（尚未开始处理）",
                pause_requested=False,
                resume_stage=pre_resume,
                finished_at=None,
            )
            _log(self.task_id, existing.stage or "queued", "任务在开始前被暂停", "warn")
            return

        if _is_cancelled(self.task_id):
            _update_task(
                self.task_id,
                status="canceled",
                stage="canceled",
                message="任务已取消",
                finished_at=utcnow(),
                resume_stage="",
                pause_requested=False,
            )
            return

        resume = ctx.resume_stage
        with session_scope() as db:
            task = db.get(Task, self.task_id)
            previous_progress = task.progress if task else 0
            started_at = task.started_at if task else None
        if resume:
            self.stage = resume
            self.ctx.reported_progress = previous_progress
            _update_task(
                self.task_id,
                status="running",
                stage=resume,
                message=f"从「{STAGE_LABELS.get(resume, resume)}」检查点继续",
                started_at=started_at or utcnow(),
                finished_at=None,
                error=None,
                cancel_requested=False,
                pause_requested=False,
            )
            _log(self.task_id, resume, f"从检查点继续：{STAGE_LABELS.get(resume, resume)}")
        else:
            _update_task(
                self.task_id,
                status="running",
                stage="prepare",
                progress=1,
                message="开始处理",
                started_at=utcnow(),
                finished_at=None,
                error=None,
                cancel_requested=False,
                pause_requested=False,
                resume_stage="",
            )
            self.report(2, "清理上一次的中间文件", stage="prepare", log=True)
            self._clean_derived_files()

        try:
            self._execute(resume)
        except TaskPaused as exc:
            resume_stage = exc.resume_stage or self.stage
            if resume_stage == self.stage:
                self._clean_partial(self.stage)
            if resume_stage == self.stage:
                paused_message = f"已暂停（{STAGE_LABELS.get(self.stage, self.stage)}），继续将重做该步骤"
            else:
                paused_message = (
                    f"已暂停；继续后从「{STAGE_LABELS.get(resume_stage, resume_stage)}」开始"
                )
            _update_task(
                self.task_id,
                status="paused",
                stage=self.stage,
                message=paused_message,
                pause_requested=False,
                resume_stage=resume_stage,
                finished_at=None,
            )
            _log(
                self.task_id,
                self.stage,
                f"任务已暂停，将从「{STAGE_LABELS.get(resume_stage, resume_stage)}」检查点继续",
                "warn",
            )
        except TaskCancelled:
            _update_task(
                self.task_id,
                status="canceled",
                stage="canceled",
                progress=self.ctx.reported_progress if self.ctx.reported_progress > 0 else 0,
                message="任务已终止",
                finished_at=utcnow(),
                resume_stage="",
                pause_requested=False,
            )
            _log(self.task_id, "canceled", "任务已终止", "warn")
            self._cleanup_workdir(keep_source=True)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            detail = str(exc).strip() or exc.__class__.__name__
            traceback.print_exc()
            _update_task(
                self.task_id,
                status="failed",
                stage=self.stage,
                message=detail[:500],
                error=detail[:4000],
                finished_at=utcnow(),
                resume_stage="",
                pause_requested=False,
            )
            _log(self.task_id, self.stage, f"处理失败：{detail}", "error")
            self._cleanup_workdir(keep_source=True)

    def _execute(self, resume: str) -> None:
        ctx = self.ctx

        def need(stage: str) -> bool:
            return not resume or STAGE_INDEX[stage] >= STAGE_INDEX[resume]

        self.control.check("prepare", force=True)

        # 1) download ------------------------------------------------------
        source = self._reuse_source()
        if source is None:
            if not need("download"):
                raise PipelineError("缺少已下载的音频，无法从该检查点继续，请重新解析")
            self.report(4, "正在解析视频页面…", stage="download", log=True)
            source = self._download()
        else:
            _update_task(self.task_id, audio_path=str(source), audio_deleted=False)
            self.report(20, f"复用已下载音频：{source.name}", stage="download", log=True)
        if need("download"):
            self.checkpoint("extract")

        # 2) extract -------------------------------------------------------
        extracted = ctx.workdir / "extracted.wav"
        if need("extract") or not extracted.exists():
            self.report(22, "提取音轨…", stage="extract", log=True)
            audio.extract_wav(source, extracted, sample_rate=44100, channels=2)
        duration = audio.probe_duration(extracted) or ctx.duration
        if duration:
            ctx.duration = duration
            with session_scope() as db:
                task = db.get(Task, self.task_id)
                video = db.get(Video, task.video_id) if task else None
                if video and not video.duration_sec:
                    video.duration_sec = duration
        if need("extract"):
            self.checkpoint("separate")

        # 3) vocals --------------------------------------------------------
        speech_wav = extracted
        vocals = ctx.workdir / "vocals.wav"
        if ctx.options.get("bgm_separation", True):
            low_memory = asr.available_ram_gb() < 3.0
            if low_memory and need("separate"):
                self.report(29, "可用内存不足 3GB，跳过背景音乐分离", stage="separate", log=True)
                _log(self.task_id, "separate", "可用内存不足，已跳过 Demucs 以保护系统", "warn")
            elif need("separate") or not vocals.exists():
                self.report(27, "分离人声与背景音乐…", stage="separate", log=True)
                try:
                    audio.separate_vocals(
                        extracted,
                        vocals,
                        device=ctx.runtime.audio_device,
                        threads=settings.cpu_threads,
                        on_progress=self.stage_progress(28, 49),
                        control=lambda: self.control.check("separate"),
                    )
                    self.report(49, "人声分离完成", stage="separate", log=True)
                except (TaskPaused, TaskCancelled):
                    raise
                except Exception as exc:  # noqa: BLE001
                    vocals.unlink(missing_ok=True)
                    self.report(49, f"人声分离失败，改用原音轨：{exc}", stage="separate", log=True)
                    _log(self.task_id, "separate", f"人声分离跳过：{exc}", "warn")
            if vocals.exists():
                speech_wav = vocals
        else:
            self.report(29, "已跳过背景音乐分离", stage="separate", log=True)
        if need("separate"):
            self.checkpoint("denoise")

        # 4) denoise -------------------------------------------------------
        speech16k = ctx.workdir / "speech16k.wav"
        denoised = ctx.workdir / "denoised.wav"
        use_denoise = bool(ctx.options.get("denoise", True))
        if use_denoise:
            if need("denoise") or not denoised.exists():
                self.report(50, "降噪处理…", stage="denoise", log=True)
                if not speech16k.exists() or need("denoise"):
                    audio.wav_to_16k_mono(speech_wav, speech16k)
                try:
                    audio.denoise(
                        speech16k,
                        denoised,
                        strength=float(ctx.options.get("denoise_strength", 0.85)),
                        on_progress=self.stage_progress(50, 59),
                        control=lambda: self.control.check("denoise"),
                    )
                    self.report(59, "降噪完成", stage="denoise", log=True)
                except (TaskPaused, TaskCancelled):
                    raise
                except Exception as exc:  # noqa: BLE001
                    denoised.unlink(missing_ok=True)
                    self.report(59, f"降噪失败，使用原始人声：{exc}", stage="denoise", log=True)
        else:
            if not speech16k.exists():
                audio.wav_to_16k_mono(speech_wav, speech16k)
            self.report(52, "已跳过降噪", stage="denoise", log=True)
        if use_denoise and denoised.exists():
            audio_in = denoised
        else:
            audio_in = speech16k
        if need("denoise"):
            self.checkpoint("normalize")

        # 5) loudness ------------------------------------------------------
        clean = ctx.workdir / "clean.wav"
        if need("normalize") or not clean.exists():
            self.report(60, "响度标准化…", stage="normalize", log=True)
            try:
                audio.loudnorm(audio_in, clean, sample_rate=16000)
            except Exception as exc:  # noqa: BLE001
                shutil.copyfile(audio_in, clean)
                _log(self.task_id, "normalize", f"响度标准化失败，使用降噪音频：{exc}", "warn")
            self.report(63, "音频预处理完成", stage="normalize", log=True)
        if need("normalize"):
            self.checkpoint("diarize")

        # 6) playback copy -------------------------------------------------
        enhanced_mp3 = ctx.workdir / "enhanced.mp3"
        if not ctx.delete_after and not enhanced_mp3.exists():
            try:
                audio.make_playback_mp3(speech_wav, enhanced_mp3)
                _update_task(self.task_id, processed_path=str(enhanced_mp3), audio_path=str(source))
            except Exception as exc:  # noqa: BLE001
                _log(self.task_id, "normalize", f"生成试听音频失败：{exc}", "warn")

        # 7) diarization ---------------------------------------------------
        turns: list[diarization.Turn] = []
        diar_json = ctx.workdir / "diarization.json"
        if ctx.options.get("diarization", True):
            if not need("diarize") and diar_json.exists():
                turns = _load_turns(diar_json)
                speakers = len({t.speaker for t in turns})
                self.report(76, f"复用说话人分离结果：{speakers} 位说话人", stage="diarize", log=True)
            elif not need("diarize"):
                turns = []
            else:
                self.report(64, "准备说话人分离模型…", stage="diarize", log=True)
                try:
                    turns = diarization.diarize(
                        clean,
                        max_speakers=int(ctx.options.get("max_speakers", 0) or 0),
                        threshold=float(ctx.runtime.diarization_threshold),
                        threads=min(8, settings.cpu_threads),
                        on_progress=self.stage_progress(64, 76),
                    )
                    speakers = len({t.speaker for t in turns})
                    self.report(76, f"说话人分离完成，识别到 {speakers} 位说话人", stage="diarize", log=True)
                except Exception as exc:  # noqa: BLE001
                    turns = []
                    self.report(76, f"说话人分离失败，将输出无说话人标记的文稿：{exc}", stage="diarize", log=True)
                    _log(self.task_id, "diarize", f"说话人分离跳过：{exc}", "warn")
                _save_turns(diar_json, turns)
        else:
            if need("diarize"):
                self.report(65, "已跳过说话人分离", stage="diarize", log=True)
            _save_turns(diar_json, [])
        if need("diarize"):
            self.checkpoint("transcribe")

        # 8) ASR -----------------------------------------------------------
        result: asr.ASRResult
        asr_json = ctx.workdir / "asr.json"
        if not need("transcribe") and asr_json.exists():
            result = _load_asr(asr_json)
            self.report(95, f"复用识别结果：{len(result.segments)} 段", stage="transcribe", log=True)
        else:
            self.report(76, "开始语音识别…", stage="transcribe", log=True)
            result = self._transcribe(clean)
            _save_asr(asr_json, result)
            self.report(95, f"识别完成，共 {len(result.segments)} 段", stage="transcribe", log=True)
        if need("transcribe"):
            self.checkpoint("finalize")

        # 9) align + persist ----------------------------------------------
        self.report(96, "整理说话人标记与时间戳…", stage="finalize", log=True)
        final_segments: list[FinalSegment] = build_segments(result.segments, turns)
        if not final_segments:
            raise PipelineError("没有生成有效文稿，请检查音频是否包含人声")

        speaker_count = len({s.speaker for s in final_segments if s.speaker})
        self._save_transcript(
            final_segments,
            language=result.language,
            asr_backend=result.backend,
            asr_model=result.model,
            speaker_count=speaker_count,
            duration=duration,
        )
        self.report(99, "文稿已保存", stage="finalize", log=True)

        # 10) cleanup ------------------------------------------------------
        audio_deleted = False
        if ctx.delete_after:
            audio_deleted = True
            self._cleanup_workdir(keep_source=False)
            _log(self.task_id, "done", "已按设置删除音频文件（文稿与讨论永久保留）", "info")
        else:
            self._cleanup_workdir(keep_source=True)
            _log(self.task_id, "done", "音频已保留，可在任务页试听或随时删除", "info")

        _update_task(
            self.task_id,
            status="completed",
            stage="done",
            progress=100,
            message="处理完成",
            finished_at=utcnow(),
            audio_deleted=audio_deleted,
            audio_path=None if audio_deleted else str(source),
            processed_path=None
            if audio_deleted
            else (str(enhanced_mp3) if enhanced_mp3.exists() else None),
            resume_stage="",
            pause_requested=False,
            cancel_requested=False,
        )
        _log(self.task_id, "done", "全部处理完成", progress=100)
        gc.collect()

    # -- steps ------------------------------------------------------------
    def _reuse_source(self) -> Path | None:
        candidates = [
            p
            for p in self.ctx.workdir.glob("source.*")
            if p.is_file() and p.suffix.lower() != ".part" and p.stat().st_size > 0
        ]
        return candidates[0] if candidates else None

    def _download(self) -> Path:
        def on_progress(frac: float, message: str) -> None:
            self.report(4 + 16 * frac, f"下载音频 {int(frac * 100)}% {message}".strip())

        source = downloader.download_audio(
            self.ctx.url,
            self.ctx.workdir,
            proxy=settings.proxy,
            cookies_file=settings.cookies_file,
            on_progress=on_progress,
        )
        self.report(22, f"音频下载完成：{source.name}", stage="download", log=True)
        _update_task(self.task_id, audio_path=str(source), audio_deleted=False)
        return source

    def _transcribe(self, clean: Path) -> asr.ASRResult:
        ctx = self.ctx
        backend = (ctx.asr_backend or "local").lower()
        if backend == "cloud":
            cloud_audio = ctx.workdir / "cloud.mp3"
            self.report(77, "压缩音频用于云端识别…")
            asr.prepare_cloud_audio(clean, cloud_audio)
            try:
                return asr.transcribe_cloud(
                    cloud_audio,
                    base_url=ctx.runtime.cloud_asr_base_url,
                    api_key=ctx.runtime.cloud_asr_api_key,
                    model=ctx.runtime.cloud_asr_model,
                    language=str(ctx.options.get("language") or "auto"),
                    prompt=f"{ctx.title}。{ctx.description[:200]}",
                    on_progress=self.stage_progress(78, 94),
                    control=lambda: self.control.check("transcribe"),
                )
            finally:
                cloud_audio.unlink(missing_ok=True)

        return asr.transcribe_local(
            clean,
            model_name=ctx.asr_model,
            language=str(ctx.options.get("language") or "auto"),
            threads=settings.cpu_threads,
            compute_type=ctx.runtime.asr_compute_type,
            device=ctx.runtime.asr_device,
            initial_prompt=f"{ctx.title}。{ctx.description[:200]}",
            hotwords=ctx.title,
            on_progress=self.stage_progress(77, 94),
            control=lambda: self.control.check("transcribe"),
        )

    def _save_transcript(
        self,
        segments: list[FinalSegment],
        *,
        language: str,
        asr_backend: str,
        asr_model: str,
        speaker_count: int,
        duration: float,
    ) -> None:
        text = render_text(segments)
        with session_scope() as db:
            task = db.get(Task, self.task_id)
            if task is None:
                return
            existing = db.query(Transcript).filter(Transcript.task_id == self.task_id).one_or_none()
            if existing:
                db.delete(existing)
                db.flush()
            transcript = Transcript(
                task_id=self.task_id,
                language=language or "",
                asr_backend=asr_backend,
                asr_model=asr_model,
                text=text,
                duration_sec=float(duration or 0),
                segment_count=len(segments),
                speaker_count=speaker_count,
            )
            db.add(transcript)
            db.flush()
            db.add_all(
                [
                    Segment(
                        transcript_id=transcript.id,
                        idx=i,
                        start_ms=int(round(seg.start * 1000)),
                        end_ms=int(round(seg.end * 1000)),
                        speaker=seg.speaker,
                        text=seg.text,
                    )
                    for i, seg in enumerate(segments)
                ]
            )

    def _clean_partial(self, stage: str) -> None:
        name = {
            "separate": "vocals.wav",
            "denoise": "denoised.wav",
            "normalize": "clean.wav",
            "diarize": "diarization.json",
            "transcribe": "asr.json",
        }.get(stage)
        if name:
            (self.ctx.workdir / name).unlink(missing_ok=True)
        (self.ctx.workdir / "cloud.mp3").unlink(missing_ok=True)
        if stage == "separate":
            (self.ctx.workdir / "enhanced.mp3").unlink(missing_ok=True)

    def _clean_derived_files(self) -> None:
        for name in (
            "extracted.wav",
            "vocals.wav",
            "speech16k.wav",
            "denoised.wav",
            "clean.wav",
            "cloud.mp3",
            "enhanced.mp3",
            "diarization.json",
            "asr.json",
        ):
            (self.ctx.workdir / name).unlink(missing_ok=True)

    def _cleanup_workdir(self, *, keep_source: bool) -> None:
        workdir = self.ctx.workdir
        if not workdir.exists():
            return
        if not keep_source:
            shutil.rmtree(workdir, ignore_errors=True)
            return
        for path in workdir.iterdir():
            if not path.is_file():
                continue
            if path.suffix.lower() in {".wav", ".json"} or path.name.endswith(".part"):
                path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# checkpoint helpers
# ---------------------------------------------------------------------------
def _save_turns(path: Path, turns: list[diarization.Turn]) -> None:
    path.write_text(
        json.dumps(
            [{"start": t.start, "end": t.end, "speaker": t.speaker} for t in turns],
            ensure_ascii=False,
        ),
        "utf-8",
    )


def _load_turns(path: Path) -> list[diarization.Turn]:
    try:
        data = json.loads(path.read_text("utf-8"))
        return [
            diarization.Turn(start=float(item["start"]), end=float(item["end"]), speaker=int(item["speaker"]))
            for item in data
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _save_asr(path: Path, result: asr.ASRResult) -> None:
    payload = {
        "language": result.language,
        "model": result.model,
        "backend": result.backend,
        "segments": [
            {
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
                "words": [{"start": w.start, "end": w.end, "text": w.text} for w in seg.words],
            }
            for seg in result.segments
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def _load_asr(path: Path) -> asr.ASRResult:
    data = json.loads(path.read_text("utf-8"))
    segments = [
        asr.RawSegment(
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item.get("text") or ""),
            words=[
                asr.Word(start=float(w["start"]), end=float(w["end"]), text=str(w.get("text") or ""))
                for w in item.get("words") or []
            ],
        )
        for item in data.get("segments") or []
    ]
    return asr.ASRResult(
        segments=segments,
        language=str(data.get("language") or ""),
        model=str(data.get("model") or ""),
        backend=str(data.get("backend") or "local"),
    )


def run_task(task_id: str) -> None:
    ctx = _load_job(task_id)
    if ctx is None:
        return
    Pipeline(ctx).run()


def recover_orphans() -> int:
    """Mark tasks left in 'running' by a crash/restart as failed; paused tasks survive."""
    with session_scope() as db:
        stale = db.query(Task).filter(Task.status.in_(["running", "queued"])).all()
        for task in stale:
            if task.status == "running":
                task.status = "failed"
                task.error = "服务重启，任务中断，请重试"
                task.message = "服务重启导致中断"
                task.finished_at = utcnow()
                task.pause_requested = False
                task.resume_stage = ""
                db.add(
                    TaskEvent(
                        task_id=task.id,
                        stage=task.stage,
                        level="error",
                        message="服务重启，任务中断",
                    )
                )
        return len(stale)


def prepare_models_link() -> None:
    """Expose a pre-downloaded faster-whisper model in the local model dir."""
    target = settings.model_dir / "asr" / "large-v3"
    if target.exists():
        return
    cache = Path.home() / ".cache" / "huggingface" / "hub"
    snapshots = sorted(cache.glob("models--Systran--faster-whisper-large-v3/snapshots/*"))
    if snapshots and (snapshots[0] / "model.bin").exists():
        try:
            target.symlink_to(snapshots[0], target_is_directory=True)
        except OSError:
            pass
