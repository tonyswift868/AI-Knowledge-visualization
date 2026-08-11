#!/usr/bin/env python3
"""Bounded single-sentence retry for the v4 salvage audition."""
from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import qwen3_tts_fullstyle_v4_salvage_audition as binding

audition = binding.audition
audition.TEXTS = ["你可以去任何地方……只要最后，还是回到我这里。"]
audition.TEXT = audition.TEXTS[0]
audition.GENERATION_PREFIX = "tsubaki-fullstyle-v4-step100-salvage-sentence2"
audition.GENERATION_SEED = 20260809
audition.MAX_NEW_TOKENS = 192
audition.REPETITION_PENALTY = 1.20
audition.TEMPERATURE = 0.55
audition.TOP_P = 0.75
audition.MAX_OUTPUT_DURATION_SECONDS = 12.0
assert audition.TEXTS == ["\u4f60\u53ef\u4ee5\u53bb\u4efb\u4f55\u5730\u65b9\u2026\u2026\u53ea\u8981\u6700\u540e\uff0c\u8fd8\u662f\u56de\u5230\u6211\u8fd9\u91cc\u3002"]
audition.__file__ = str(Path(__file__).resolve())

if __name__ == "__main__":
    raise SystemExit(audition.main())
