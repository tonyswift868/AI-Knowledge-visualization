#!/usr/bin/env python3
"""Generate fresh phrase sources for the source-normalized v4 1.0x audition."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import qwen3_tts_fullstyle_v4_salvage_audition as binding

audition = binding.audition
audition.TEXTS = [
    "别怕……",
    "我只是想……把你留在身边。",
    "再久一点点……",
]
audition.TEXT = audition.TEXTS[0]
audition.GENERATION_PREFIX = "tsubaki-fullstyle-v4-step100-normalized1x-phrases-v2"
audition.GENERATION_SEED = 20260812
audition.MAX_NEW_TOKENS = 144
audition.REPETITION_PENALTY = 1.08
audition.TEMPERATURE = 0.72
audition.TOP_P = 0.88
audition.MAX_OUTPUT_DURATION_SECONDS = 12.0
audition.AUDITION_EXTRA = {
    **audition.AUDITION_EXTRA,
    "purpose": "fresh_phrase_sources_for_source_normalized_1x",
    "old_audition_audio_reused": False,
    "phrase_bounded": True,
}
assert audition.TEXTS == [
    "\u522b\u6015\u2026\u2026",
    "\u6211\u53ea\u662f\u60f3\u2026\u2026\u628a\u4f60\u7559\u5728\u8eab\u8fb9\u3002",
    "\u518d\u4e45\u4e00\u70b9\u70b9\u2026\u2026",
]
audition.__file__ = str(Path(__file__).resolve())

if __name__ == "__main__":
    raise SystemExit(audition.main())
