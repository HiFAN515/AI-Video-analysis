"""Proxy remote cover images so the browser never has to reach CDNs directly."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ..config import settings

router = APIRouter(tags=["media"])

ALLOWED_SUFFIXES = (
    "hdslb.com",
    "bilibili.com",
    "b23.tv",
    "ytimg.com",
    "ggpht.com",
    "youtube.com",
    "googleusercontent.com",
)
MAX_BYTES = 12 * 1024 * 1024
CACHE_TTL = 7 * 24 * 3600


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return settings.data_dir / "cache" / "covers" / digest[:2] / f"{digest}.img"


def _cache_meta(path: Path) -> Path:
    return path.with_suffix(".meta")


def _validate(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="图片地址不合法")
    host = (parsed.hostname or "").lower()
    if not any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_SUFFIXES):
        raise HTTPException(status_code=400, detail="不允许代理该图片域名")
    return url


@router.get("/media/cover")
def cover(url: str = Query(min_length=8, max_length=2048)):
    _validate(url)
    path = _cache_path(url)
    if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL:
        meta = _cache_meta(path)
        media_type = meta.read_text("utf-8").strip() if meta.exists() else "image/jpeg"
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "public, max-age=86400"})

    proxy = settings.proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    try:
        with httpx.Client(
            follow_redirects=True,
            proxy=proxy or None,
            trust_env=False,
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0),
        ) as client:
            with client.stream("GET", url, headers={"User-Agent": "Mozilla/5.0 VideoMind/1.0"}) as response:
                if response.status_code >= 400:
                    raise HTTPException(status_code=502, detail="封面图获取失败")
                content_type = response.headers.get("content-type", "image/jpeg")
                if not content_type.startswith("image/"):
                    raise HTTPException(status_code=502, detail="返回内容不是图片")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise HTTPException(status_code=502, detail="图片过大")
                    chunks.append(chunk)
                data = b"".join(chunks)
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"封面图获取失败：{exc}") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    _cache_meta(path).write_text(content_type, "utf-8")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
