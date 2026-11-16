"""Bilibili / video download helpers built on yt-dlp."""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ProgressFn = Callable[[float, str], None]

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
_AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)
_BARE_BV_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")
_BARE_AV_RE = re.compile(r"^av\d+$", re.IGNORECASE)

BILIBILI_HOSTS = ("bilibili.com", "b23.tv", "bili2233.cn")
YOUTUBE_HOSTS = ("youtube.com", "youtu.be")


class DownloadError(RuntimeError):
    pass


def normalize_url(raw: str) -> str:
    """Turn bare BV ids / share text into a usable URL."""
    text = (raw or "").strip()
    if not text:
        raise DownloadError("请输入视频链接")

    # A whole share message often contains the URL inside it.
    for token in re.findall(r"https?://[^\s，。；、）)】\]】\"']+", text):
        text = token
        break

    if text.startswith("//"):
        text = "https:" + text
    if _BARE_BV_RE.match(text):
        return f"https://www.bilibili.com/video/{text}"
    if _BARE_AV_RE.match(text):
        return f"https://www.bilibili.com/video/{text.lower()}"

    if not text.startswith(("http://", "https://")):
        bv = _BV_RE.search(text)
        if bv:
            return f"https://www.bilibili.com/video/{bv.group(1)}"
        av = _AV_RE.search(text)
        if av:
            return f"https://www.bilibili.com/video/av{av.group(1)}"
        raise DownloadError("链接格式不正确，请粘贴完整的视频链接")

    return text


def detect_platform(url: str) -> str:
    lowered = url.lower()
    if any(host in lowered for host in BILIBILI_HOSTS):
        return "bilibili"
    if any(host in lowered for host in YOUTUBE_HOSTS):
        return "youtube"
    return "generic"


def _base_opts(proxy: str, cookies_file: str) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "nocheckcertificate": True,
        "ignoreerrors": False,
    }
    if proxy:
        opts["proxy"] = proxy
    if cookies_file and Path(cookies_file).exists():
        opts["cookiefile"] = cookies_file
    return opts


def _pick_entry(info: dict[str, Any]) -> dict[str, Any]:
    if info.get("_type") == "playlist" and info.get("entries"):
        entries = [e for e in info["entries"] if e]
        if entries:
            return entries[0]
    return info


def _parse_published(info: dict[str, Any]) -> datetime | None:
    ts = info.get("release_timestamp") or info.get("timestamp")
    if ts:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc)
        except (ValueError, OSError):
            pass
    upload_date = info.get("upload_date")
    if upload_date and len(str(upload_date)) == 8:
        try:
            return datetime.strptime(str(upload_date), "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _remote_identity(platform: str, info: dict[str, Any]) -> tuple[str, int]:
    raw_id = str(info.get("id") or "")
    page = int(info.get("page") or info.get("playlist_index") or 1)
    if platform == "bilibili":
        bv = _BV_RE.search(str(info.get("webpage_url") or "")) or _BV_RE.search(raw_id)
        if bv:
            return bv.group(1), page
        av = _AV_RE.search(raw_id)
        if av:
            return f"av{av.group(1)}", page
    return raw_id or "unknown", page


def probe(url: str, *, proxy: str = "", cookies_file: str = "") -> dict[str, Any]:
    """Fetch video metadata without downloading."""
    import yt_dlp

    clean = normalize_url(url)
    platform = detect_platform(clean)
    opts = _base_opts(proxy, cookies_file)
    opts.update({"skip_download": True, "extract_flat": False})
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _pick_entry(ydl.extract_info(clean, download=False))
    except Exception as exc:  # yt-dlp raises many exception types
        raise DownloadError(_friendly_error(exc)) from exc

    remote_id, page = _remote_identity(platform, info)
    webpage_url = str(info.get("webpage_url") or clean)
    if platform == "bilibili" and remote_id.startswith("BV"):
        webpage_url = f"https://www.bilibili.com/video/{remote_id}"
        if page > 1:
            webpage_url += f"?p={page}"

    return {
        "url": webpage_url,
        "platform": platform,
        "remote_id": remote_id,
        "page": page,
        "title": str(info.get("title") or "未命名视频"),
        "uploader": str(info.get("uploader") or info.get("channel") or ""),
        "cover_url": str(info.get("thumbnail") or "").replace("http://", "https://"),
        "duration_sec": float(info.get("duration") or 0),
        "description": str(info.get("description") or ""),
        "published_at": _parse_published(info),
    }


def download_audio(
    url: str,
    out_dir: Path,
    *,
    proxy: str = "",
    cookies_file: str = "",
    on_progress: ProgressFn | None = None,
) -> Path:
    """Download the best audio stream into ``out_dir`` and return the file path."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    clean = normalize_url(url)

    def hook(data: dict[str, Any]) -> None:
        if not on_progress:
            return
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            done = data.get("downloaded_bytes") or 0
            frac = min(0.99, done / total) if total else 0.0
            speed = data.get("speed") or 0
            speed_txt = f"{speed / 1024 / 1024:.1f} MB/s" if speed else ""
            on_progress(frac, speed_txt)
        elif status == "finished":
            on_progress(1.0, "下载完成，正在转换")

    opts = _base_opts(proxy, cookies_file)
    opts.update(
        {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": str(out_dir / "source.%(ext)s"),
            "progress_hooks": [hook],
            "overwrites": True,
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = _pick_entry(ydl.extract_info(clean, download=True))
            filename = info.get("_filename") or info.get("filepath")
            if not filename:
                filename = ydl.prepare_filename(info)
    except Exception as exc:
        raise DownloadError(_friendly_error(exc)) from exc

    path = Path(filename)
    if not path.exists():
        candidates = sorted(out_dir.glob("source.*"))
        if not candidates:
            raise DownloadError("音频下载完成但未找到输出文件")
        path = candidates[0]
    return path


def _friendly_error(exc: Exception) -> str:
    text = str(exc).strip().replace("\n", " ")
    lowered = text.lower()
    if "ffmpeg" in lowered and "not found" in lowered:
        return "未找到 ffmpeg，请先安装 ffmpeg"
    if "sign in" in lowered or "login" in lowered or "cookie" in lowered:
        return "该视频需要登录才能访问，请在设置中配置浏览器 Cookie 文件"
    if "private" in lowered or "会员" in text or "大会员" in text:
        return "该视频为私有/会员内容，当前无法下载"
    if "unsupported url" in lowered:
        return "暂不支持该链接，请确认是视频页面链接"
    if "timed out" in lowered or "timeout" in lowered:
        return "连接视频网站超时，请检查网络或代理设置"
    if len(text) > 300:
        text = text[:300] + "…"
    return text or "下载失败"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
