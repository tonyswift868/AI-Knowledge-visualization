from __future__ import annotations

import hashlib
import re
from typing import Any


def _normalise(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def render_verified_content(
    verified_content: str,
    character_id: str,
    story_stage: str,
    emotion: str = "neutral",
    relationship_state: dict[str, Any] | None = None,
    speech_rules: list[str] | None = None,
    forbidden_speech: list[str] | None = None,
    candidate_text: str | None = None,
) -> dict[str, Any]:
    """Return verified Tutor text unchanged unless a future renderer proves semantic preservation.

    The V1 implementation intentionally has no generative rewrite path. This makes the
    persona layer auditable: a candidate that differs from verified content falls back to
    the original instead of silently changing facts, formulas, code or citations.
    """
    source = str(verified_content or "")
    candidate = source if candidate_text is None else str(candidate_text)
    preserved = _normalise(candidate) == _normalise(source)
    return {
        "rendered_text": candidate if preserved else source,
        "source_text_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "semantic_preservation_result": "passed" if preserved else "failed_fallback",
        "style_metadata": {"character_id": character_id, "story_stage": story_stage, "emotion": emotion, "maximum_style_change": "none"},
        "continuity_warnings": [] if preserved else ["Persona candidate changed verified content; original Tutor text was used."],
    }
