"""Turn ASR output into speaker-labelled, timestamped transcript segments."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

from .asr import RawSegment
from .diarization import Turn

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])")
_SPACE_PUNCT_RE = re.compile(r"\s+([，。！？、；：,.!?;:）)\】」』])")
_OPEN_PUNCT_RE = re.compile(r"([（(\【「『])\s+")
_CJK_PUNCT_SPACE_RE = re.compile(rf"(?<=[，。！？、；：])\s+(?=[{_CJK}])")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


@dataclass
class FinalSegment:
    start: float
    end: float
    speaker: str
    text: str


def clean_text(text: str) -> str:
    text = (text or "").replace("\u200b", "").replace("\ufeff", "").strip()
    text = _CJK_SPACE_RE.sub("", text)
    text = _SPACE_PUNCT_RE.sub(r"\1", text)
    text = _OPEN_PUNCT_RE.sub(r"\1", text)
    text = _CJK_PUNCT_SPACE_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


def _speaker_map(turns: list[Turn]) -> dict[int, str]:
    ordered: list[int] = []
    for turn in sorted(turns, key=lambda t: t.start):
        if turn.speaker not in ordered:
            ordered.append(turn.speaker)
    return {raw: f"说话人 {i + 1}" for i, raw in enumerate(ordered)}


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _speaker_at(turns: list[Turn], start: float, end: float) -> int | None:
    if not turns:
        return None
    best, best_score = None, 0.0
    for turn in turns:
        score = _overlap(start, end, turn.start, turn.end)
        if score > best_score:
            best, best_score = turn.speaker, score
    if best is not None:
        return best
    center = (start + end) / 2
    nearest = min(turns, key=lambda t: abs((t.start + t.end) / 2 - center))
    if abs((nearest.start + nearest.end) / 2 - center) <= 3.0:
        return nearest.speaker
    return None


def _split_by_words(seg: RawSegment, turns: list[Turn]) -> list[tuple[float, float, int | None, str]]:
    """Split a Whisper segment wherever the diarized speaker changes."""
    channels: list[tuple[float, float, int | None, str]] = []
    current: list[str] = []
    cur_start = seg.start
    cur_end = seg.end
    cur_speaker: int | None = None

    for word in seg.words:
        speaker = _speaker_at(turns, word.start, word.end)
        if current and speaker != cur_speaker:
            text = clean_text("".join(current))
            if text:
                channels.append((cur_start, cur_end, cur_speaker, text))
            current = []
        if not current:
            cur_start = word.start
            cur_speaker = speaker
        current.append(word.text)
        cur_end = word.end

    if current:
        text = clean_text("".join(current))
        if text:
            channels.append((cur_start, cur_end, cur_speaker, text))
    return channels


def build_segments(raw: list[RawSegment], turns: list[Turn]) -> list[FinalSegment]:
    labels = _speaker_map(turns)
    output: list[FinalSegment] = []

    for seg in raw:
        chunks: list[tuple[float, float, int | None, str]]
        if seg.words and turns:
            chunks = _split_by_words(seg, turns)
        else:
            speaker = _speaker_at(turns, seg.start, seg.end) if turns else None
            text = clean_text(seg.text)
            chunks = [(seg.start, seg.end, speaker, text)] if text else []

        for start, end, speaker, text in chunks:
            label = labels.get(speaker, "") if speaker is not None else ""
            output.append(FinalSegment(start=start, end=end, speaker=label, text=text))

    output.sort(key=lambda s: (s.start, s.end))
    return _merge_adjacent(output)


def _merge_adjacent(segments: list[FinalSegment], gap: float = 0.5) -> list[FinalSegment]:
    if not segments:
        return []
    merged: list[FinalSegment] = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        same_speaker = prev.speaker == seg.speaker
        close = seg.start - prev.end <= gap
        short_enough = len(prev.text) + len(seg.text) <= 140
        if same_speaker and close and short_enough:
            joiner = "" if _needs_no_space(prev.text, seg.text) else " "
            prev.text = f"{prev.text}{joiner}{seg.text}".strip()
            prev.end = max(prev.end, seg.end)
        else:
            merged.append(seg)
    return merged


def _needs_no_space(left: str, right: str) -> bool:
    if not left or not right:
        return True
    if re.search(rf"[{_CJK}]$", left) or re.match(rf"[{_CJK}]", right):
        return True
    if right[0] in "，。！？、；：,.!?;:）)】」』":
        return True
    return False


def format_timestamp(seconds: float, *, hours_if_needed: bool = True) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if hours_if_needed and h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def render_text(segments: Iterable[FinalSegment], *, include_speaker: bool = True) -> str:
    blocks: list[str] = []
    for seg in segments:
        header = f"[{format_timestamp(seg.start)}]"
        if include_speaker and seg.speaker:
            header += f" {seg.speaker}"
        blocks.append(f"{header}\n{seg.text}")
    return "\n\n".join(blocks)


def render_context(segments: Iterable[FinalSegment]) -> str:
    lines = []
    for seg in segments:
        who = f"{seg.speaker}：" if seg.speaker else ""
        lines.append(f"[{format_timestamp(seg.start)}] {who}{seg.text}")
    return "\n".join(lines)


def to_srt(segments: Iterable[FinalSegment]) -> str:
    def stamp(value: float) -> str:
        value = max(0.0, value)
        h = int(value // 3600)
        m = int((value % 3600) // 60)
        s = int(value % 60)
        ms = int(round((value - math.floor(value)) * 1000))
        if ms == 1000:
            s += 1
            ms = 0
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        who = f"{seg.speaker}：" if seg.speaker else ""
        lines.append(str(i))
        lines.append(f"{stamp(seg.start)} --> {stamp(seg.end)}")
        lines.append(f"{who}{seg.text}")
        lines.append("")
    return "\n".join(lines)


def to_markdown(
    segments: Iterable[FinalSegment],
    *,
    title: str,
    url: str = "",
    duration: float = 0,
    speaker_count: int = 0,
    engine: str = "",
) -> str:
    minutes = int(duration // 60)
    header = [f"# {title or '转录文稿'}", ""]
    if url:
        header.append(f"- 来源：{url}")
    header.append(f"- 时长：{minutes} 分钟")
    if speaker_count:
        header.append(f"- 说话人：{speaker_count} 位")
    if engine:
        header.append(f"- 识别引擎：{engine}")
    header += ["", "## 转录文稿", ""]
    body = []
    for seg in segments:
        who = f"**{seg.speaker}** " if seg.speaker else ""
        body.append(f"{who}`[{format_timestamp(seg.start)}]` {seg.text}")
        body.append("")
    return "\n".join(header + body)


def to_plain(segments: Iterable[FinalSegment]) -> str:
    return "\n".join(
        f"[{format_timestamp(seg.start)}] {seg.speaker + '：' if seg.speaker else ''}{seg.text}"
        for seg in segments
    )


_TERM_RE = re.compile(rf"[{_CJK}]|[a-zA-Z0-9]+")


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    terms: set[str] = set(re.findall(r"[a-z0-9]{2,}", lowered))
    cjk = re.findall(rf"[{_CJK}]", text)
    terms.update(a + b for a, b in zip(cjk, cjk[1:]))
    terms.update(cjk)
    return terms


def select_relevant(
    segments: list[FinalSegment],
    query: str,
    *,
    budget_chars: int = 60000,
    max_segments: int = 60,
) -> list[FinalSegment]:
    """Cheap lexical retrieval used to keep chat context inside the model window."""
    if not segments:
        return []
    if len(render_context(segments)) <= budget_chars:
        return segments

    query_terms = _terms(query)
    scored: list[tuple[float, int]] = []
    for idx, seg in enumerate(segments):
        overlap = len(query_terms & _terms(seg.text))
        if overlap:
            scored.append((overlap / math.sqrt(len(seg.text) + 1), idx))
    scored.sort(reverse=True)

    picked: set[int] = set()
    for _, idx in scored[:max_segments]:
        for neighbour in (idx - 1, idx, idx + 1):
            if 0 <= neighbour < len(segments):
                picked.add(neighbour)
    # Always keep the opening context so the model knows what the video is about.
    picked.update(range(min(8, len(segments))))

    chosen = [segments[i] for i in sorted(picked)]
    while chosen and len(render_context(chosen)) > budget_chars and len(chosen) > 8:
        # Drop from the middle, keep the opening and recent context.
        chosen.pop(len(chosen) // 2)
    return chosen
