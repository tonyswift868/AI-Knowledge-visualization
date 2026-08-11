#!/usr/bin/env python3
"""Two-text offline style audition bound only to accepted singleclip-r3."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import qwen3_tts_offline_audition as audition

audition.CHECKPOINT = Path(r"E:\EV\20260808_164409.training\runs\tsubaki-singleclip-exp-v3b-freshbase-20step-r3-20260808t2025cst\checkpoints\checkpoint-step-00000020")
audition.TRAINING_PLAN_SHA256 = "26505489b022ba0f0d044dc6b287522c2c55d6988b0394ae88e0669b08a5cadd"
audition.CHECKPOINT_RECEIPT_SHA256 = "30bbdc794373c5a9998f704ad5d0801a56833e9d5aeaf38fe5ce567229d49fcb"
audition.CHECKPOINT_TREE_SHA256 = "d6395e395a98228487740fc2ac1d9e06596748d7561a738a262ac0961aa494f9"
audition.REFERENCE = Path(r"E:\EV\20260808_164409.training\generations\tsubaki-singleclip-exp-v3b-20260808t1925cst\dataset\reference\singleclip-reference.wav")
audition.REFERENCE_SHA256 = "95028df4ed1c075bdedd6b2da2f190ee0f784fb1c776c8f78e23be01748fc48e"
audition.OUTPUT_PARENT = Path(r"E:\EV\20260808_164409.training\auditions")
audition.SPEAKER = "tsubaki_singleclip_exp"
audition.TEXTS = [
    "别怕，我只是想把你留在身边，再久一点点。",
    "你可以去任何地方……只要最后，还是回到我这里。",
]
audition.TEXT = audition.TEXTS[0]
audition.GENERATION_PREFIX = "tsubaki-singleclip-r3-style-offline-audition"
assert audition.TEXTS == [
    "\u522b\u6015\uff0c\u6211\u53ea\u662f\u60f3\u628a\u4f60\u7559\u5728\u8eab\u8fb9\uff0c\u518d\u4e45\u4e00\u70b9\u70b9\u3002",
    "\u4f60\u53ef\u4ee5\u53bb\u4efb\u4f55\u5730\u65b9\u2026\u2026\u53ea\u8981\u6700\u540e\uff0c\u8fd8\u662f\u56de\u5230\u6211\u8fd9\u91cc\u3002",
]
audition.__file__ = str(Path(__file__).resolve())


if __name__ == "__main__":
    raise SystemExit(audition.main())
