"""Saved LLM endpoint profiles (visible list of what the user configured)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from urllib.parse import urlparse

from ..db import get_db
from ..models import LLMProfile, Setting
from ..schemas import (
    LLMProfileCreate,
    LLMProfileOut,
    MessageResponse,
    SettingsOut,
    TestResult,
)
from ..services.llm import LLMClient
from ..services.settings_store import RuntimeSettings, get_runtime, save_runtime, upsert_llm_profile

router = APIRouter(tags=["llm-profiles"])


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "••••"
    return f"{value[:4]}…{value[-4:]}"


def _public(profile: LLMProfile, runtime: RuntimeSettings) -> LLMProfileOut:
    active = (
        profile.base_url == runtime.llm_base_url
        and profile.model == runtime.llm_model
        and profile.api_key == runtime.llm_api_key
    )
    return LLMProfileOut(
        id=profile.id,
        name=profile.name,
        base_url=profile.base_url,
        model=profile.model,
        temperature=profile.temperature,
        api_key_set=bool(profile.api_key),
        api_key_hint=_mask(profile.api_key),
        is_active=active,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


@router.get("/llm-profiles", response_model=list[LLMProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[LLMProfileOut]:
    runtime = get_runtime(db)
    marker = db.get(Setting, "llm_profile_backfilled")
    if marker is None:
        rows = db.query(LLMProfile).order_by(LLMProfile.updated_at.desc()).all()
        if not rows and runtime.llm_api_key:
            # One-time backfill for installs that saved a key before profile history existed.
            upsert_llm_profile(db, runtime)
        db.add(Setting(key="llm_profile_backfilled", value={"done": True}))
        db.commit()
    rows = db.query(LLMProfile).order_by(LLMProfile.updated_at.desc()).all()
    return [_public(row, runtime) for row in rows]


@router.post("/llm-profiles", response_model=LLMProfileOut, status_code=201)
def save_profile(payload: LLMProfileCreate, db: Session = Depends(get_db)) -> LLMProfileOut:
    runtime = get_runtime(db)
    profile = (
        db.query(LLMProfile)
        .filter(LLMProfile.base_url == payload.base_url, LLMProfile.model == payload.model)
        .one_or_none()
    )
    if profile is None:
        if not payload.api_key.strip():
            raise HTTPException(status_code=400, detail="新增配置时必须填写 API Key")
        host = urlparse(payload.base_url).netloc or payload.base_url
        profile = LLMProfile(
            name=(payload.name or "").strip() or f"{payload.model} · {host}",
            base_url=payload.base_url,
            model=payload.model,
            api_key=payload.api_key.strip(),
            temperature=payload.temperature,
        )
        db.add(profile)
    else:
        if payload.name.strip():
            profile.name = payload.name.strip()
        if payload.api_key.strip():
            profile.api_key = payload.api_key.strip()
        profile.temperature = payload.temperature
    db.commit()
    db.refresh(profile)
    return _public(profile, get_runtime(db))


@router.post("/llm-profiles/save-current", response_model=LLMProfileOut)
def save_current(db: Session = Depends(get_db)) -> LLMProfileOut:
    runtime = get_runtime(db)
    if not runtime.llm_api_key:
        raise HTTPException(status_code=400, detail="当前配置没有 API Key，无法保存")
    profile = (
        db.query(LLMProfile)
        .filter(
            LLMProfile.base_url == runtime.llm_base_url,
            LLMProfile.model == runtime.llm_model,
        )
        .one_or_none()
    )
    host = urlparse(runtime.llm_base_url).netloc or runtime.llm_base_url
    if profile is None:
        profile = LLMProfile(
            name=f"{runtime.llm_model} · {host}",
            base_url=runtime.llm_base_url,
            model=runtime.llm_model,
            api_key=runtime.llm_api_key,
            temperature=runtime.llm_temperature,
        )
        db.add(profile)
    else:
        profile.api_key = runtime.llm_api_key
        profile.temperature = runtime.llm_temperature
    db.commit()
    db.refresh(profile)
    return _public(profile, runtime)


@router.post("/llm-profiles/{profile_id}/activate", response_model=SettingsOut)
def activate_profile(profile_id: str, db: Session = Depends(get_db)) -> SettingsOut:
    profile = db.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    runtime = get_runtime(db)
    runtime.llm_base_url = profile.base_url
    runtime.llm_model = profile.model
    runtime.llm_api_key = profile.api_key
    runtime.llm_temperature = profile.temperature
    save_runtime(db, runtime)
    return runtime.to_public()


@router.post("/llm-profiles/{profile_id}/test", response_model=TestResult)
def test_profile(profile_id: str, db: Session = Depends(get_db)) -> TestResult:
    profile = db.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    runtime = get_runtime(db)
    client = LLMClient(runtime)
    ok, message, latency = client.test(
        base_url=profile.base_url, model=profile.model, api_key=profile.api_key
    )
    return TestResult(ok=ok, message=message, latency_ms=latency)


@router.delete("/llm-profiles/{profile_id}", response_model=MessageResponse)
def delete_profile(profile_id: str, db: Session = Depends(get_db)) -> MessageResponse:
    profile = db.get(LLMProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.delete(profile)
    db.commit()
    return MessageResponse(message="配置已删除")
