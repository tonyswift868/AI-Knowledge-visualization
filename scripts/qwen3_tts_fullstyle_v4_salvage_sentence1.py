#!/usr/bin/env python3
"""Bounded clean sentence-one generation for the v4 salvage delivery pair."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import qwen3_tts_fullstyle_v4_salvage_audition as binding

audition = binding.audition
audition.TEXTS = ["别怕，我只是想把你留在身边，再久一点点。"]
audition.TEXT = audition.TEXTS[0]
audition.GENERATION_PREFIX = "tsubaki-fullstyle-v4-step100-salvage-sentence1"
audition.GENERATION_SEED = 20260810
audition.MAX_NEW_TOKENS = 192
audition.REPETITION_PENALTY = 1.20
audition.TEMPERATURE = 0.55
audition.TOP_P = 0.75
audition.MAX_OUTPUT_DURATION_SECONDS = 12.0
assert audition.TEXTS == ["\u522b\u6015\uff0c\u6211\u53ea\u662f\u60f3\u628a\u4f60\u7559\u5728\u8eab\u8fb9\uff0c\u518d\u4e45\u4e00\u70b9\u70b9\u3002"]
audition.__file__ = str(Path(__file__).resolve())

if __name__ == "__main__":
    raise SystemExit(audition.main())
