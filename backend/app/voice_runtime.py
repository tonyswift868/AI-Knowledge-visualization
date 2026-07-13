from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .orm import NarrativeVoiceCacheORM, SessionLocal, init_orm


def synthesize_voice(text: str, voice_profile_id: str = "", provider: str = "fake", style: str = "neutral") -> dict[str, Any]:
    """FakeVoiceProvider contract: cache the request and continue with text when audio is unavailable."""
    init_orm()
    clean_text = str(text or "").strip()
    text_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
    cache_key = hashlib.sha256(f"{text_hash}:{voice_profile_id}:{provider}:{style}".encode("utf-8")).hexdigest()
    with SessionLocal() as session:
        cached = session.get(NarrativeVoiceCacheORM, cache_key)
        if cached:
            return {"cache_key": cache_key, "provider": cached.provider, "voice_profile_id": cached.voice_profile_id, "status": cached.status, "audio_url": None, "fallback": "text", "cached": True}
        session.add(NarrativeVoiceCacheORM(cache_key=cache_key, text_hash=text_hash, voice_profile_id=voice_profile_id or "none", provider="fake", audio_uri="", status="text_fallback", duration_ms=0, created_at=datetime.now(timezone.utc)))
        session.commit()
    return {"cache_key": cache_key, "provider": "fake", "voice_profile_id": voice_profile_id or "none", "status": "text_fallback", "audio_url": None, "fallback": "text", "cached": False}
