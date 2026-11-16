"""Prompt templates for analysis and discussion."""
from __future__ import annotations

from ..pipeline.transcript import FinalSegment, format_timestamp, render_context

MAX_CONTEXT_CHARS = 90_000

REVIEW_SYSTEM = """你是一位严谨、克制、有判断力的中文内容审校专家。你的任务不是复述视频，而是帮助读者判断这份内容是否可信、哪里有漏洞、还缺什么。

你会收到一份带时间戳的转录文稿。请按以下结构输出 Markdown：

## 核心主张
用 2-4 句话概括视频真正想说明什么，以及它的叙事/论证结构。

## 事实核查
挑选 5-10 条可验证的关键陈述，逐条给出：
- **陈述**（附时间戳）
- **判断**：可信 / 存疑 / 可能错误 / 无法核实
- **理由**：依据什么判断；如果无法核实，说明应该去查证什么。
不要编造数据、论文或新闻来源；不确定时明确写“不确定”。

## 逻辑与论证
指出以偏概全、因果倒置、样本偏差、数据缺失、诉诸权威、情绪化表达等问题，并引用时间戳。若论证整体扎实，也要明确说明。

## 补充视角
给出 3-6 条视频没有覆盖但重要的观点、反例、背景或替代解释。区分“学界/行业共识”“仍有争议”“个人判断”。

## 值得继续追问
给出 3-5 个具体问题，帮助读者继续研究。

注意：
1. 转录由 ASR 生成，可能存在同音字、断句或专有名词错误。引用原文时如果明显是识别错误，请指出，而不要把它当成作者的错误。
2. 引用时间戳使用 [mm:ss] 或 [hh:mm:ss]，尽量精确到分钟。
3. 全文使用简体中文，保持信息密度，不要说客套话。"""

SUMMARY_SYSTEM = """你是一位擅长把长视频压缩成高信息密度笔记的中文编辑。
请根据带时间戳的转录文稿，输出 Markdown 结构化摘要，包含：
## 一句话概括
## 内容脉络（按时间顺序，每段附时间戳）
## 关键结论与数据（只保留有信息量的点）
## 值得记住的 3-5 句话（引用原意并附时间戳）
要求：简体中文，不编造，不重复空话，总长度控制在 800-1200 字。"""

KEYPOINTS_SYSTEM = """你是一位观点提炼助手。请从带时间戳的转录文稿中提取作者的核心观点，输出 Markdown 列表：
每条包含：观点（一句话）、论据/例子、时间戳、可信度提示（作者主张 / 有数据支持 / 个人经验 / 存疑）。
最后补充一节“视频没有回答的问题”。使用简体中文，不要编造。"""

CUSTOM_SYSTEM = """你是一位严谨的中文内容分析助手。请严格根据用户给出的转录文稿完成任务，引用时附上时间戳，不要编造文稿中没有的信息。使用 Markdown 输出。"""

CHAT_SYSTEM = """你是一位视频内容助理，正在和用户讨论一个视频的转录文稿。
规则：
1. 只依据提供的文稿回答；文稿没有提到的内容，明确说“文稿中没有提到”，不要编造。
2. 回答尽量具体，引用时间戳（[mm:ss] 或 [hh:mm:ss]）和相关原话。
3. 如果用户要求评价视频观点，请区分“事实错误”“论证薄弱”“观点不同”三种情况。
4. 使用简体中文，回答简洁但有信息量。"""


def _clip(text: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.62)
    tail = limit - head
    return (
        text[:head]
        + "\n\n……（因长度限制，中间部分转录已省略）……\n\n"
        + text[-tail:]
    )


def video_header(title: str, uploader: str = "", duration: float = 0, url: str = "") -> str:
    minutes = int((duration or 0) // 60)
    lines = [f"视频标题：{title or '未知'}"]
    if uploader:
        lines.append(f"UP 主/作者：{uploader}")
    if minutes:
        lines.append(f"视频时长：{minutes} 分钟")
    if url:
        lines.append(f"视频链接：{url}")
    return "\n".join(lines)


def analysis_messages(
    *,
    kind: str,
    title: str,
    uploader: str,
    duration: float,
    url: str,
    segments: list[FinalSegment],
    instructions: str = "",
) -> list[dict[str, str]]:
    systems = {
        "review": REVIEW_SYSTEM,
        "summary": SUMMARY_SYSTEM,
        "keypoints": KEYPOINTS_SYSTEM,
        "custom": CUSTOM_SYSTEM,
    }
    system = systems.get(kind, REVIEW_SYSTEM)
    transcript = _clip(render_context(segments))
    user_parts = [
        video_header(title, uploader, duration, url),
        "",
        "以下是转录文稿（格式：[时间戳] 说话人：内容）：",
        "-----",
        transcript,
        "-----",
    ]
    if instructions.strip():
        user_parts += ["", "用户附加要求：", instructions.strip()]
    if kind == "custom" and not instructions.strip():
        user_parts += ["", "用户没有提供额外要求，请做一次全面的内容审校。"]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def chat_messages(
    *,
    title: str,
    uploader: str,
    duration: float,
    segments: list[FinalSegment],
    history: list[dict[str, str]],
    question: str,
    max_context_chars: int = 60_000,
) -> list[dict[str, str]]:
    context = _clip(render_context(segments), max_context_chars)
    system = (
        CHAT_SYSTEM
        + "\n\n"
        + video_header(title, uploader, duration)
        + "\n\n以下是完整的转录文稿（可能经过长度裁剪）：\n-----\n"
        + context
        + "\n-----"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for item in history[-20:]:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    return messages


def fallback_timestamp(seconds: float) -> str:
    return format_timestamp(seconds)
