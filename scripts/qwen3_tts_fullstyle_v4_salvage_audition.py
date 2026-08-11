#!/usr/bin/env python3
"""Audition-only wrapper for the independently verified fullstyle-v4 step100 salvage."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import qwen3_tts_offline_audition as audition

audition.CHECKPOINT = Path(r"E:\EV\20260808_164409.training\runs\tsubaki-singleclip-fullstyle-v4-freshbase-100step-r1-20260808t1915cst\checkpoints\checkpoint-step-00000100")
audition.TRAINING_PLAN_SHA256 = "3f5ca52db6a33c12640940a9b8c4f6a6a46fe681d43f0afb9323b43da621b54d"
audition.CHECKPOINT_RECEIPT_SHA256 = "f482976299b7a3f506fb243ba96cb7b6bbf475384640c5dbabbd03fc5a2c0a67"
audition.CHECKPOINT_TREE_SHA256 = "f71805827f5fc8230a7ad056c49f86cd870d90e0765557b109b9af1a04dea2a5"
audition.REFERENCE = Path(r"E:\EV\20260808_164409.training\generations\tsubaki-singleclip-fullstyle-v4-20260808t1850cst\dataset\reference\singleclip-reference.wav")
audition.REFERENCE_SHA256 = "8b155e1d52e9535ebc73799dbe56e222ae70f1b8be4ac1429670736e8984b9dc"
audition.OUTPUT_PARENT = Path(r"E:\EV\20260808_164409.training\auditions")
audition.SPEAKER = "tsubaki_singleclip_fullstyle_v4"
audition.TEXTS = [
    "别怕，我只是想把你留在身边，再久一点点。",
    "你可以去任何地方……只要最后，还是回到我这里。",
]
audition.TEXT = audition.TEXTS[0]
audition.GENERATION_PREFIX = "tsubaki-fullstyle-v4-step100-salvage"
audition.MAX_NEW_TOKENS = 256
audition.REPETITION_PENALTY = 1.15
audition.AUDITION_SCOPE = "private_local_nonrelease_audition_only_salvage_from_failed_owner"
audition.AUDITION_EXTRA = {
    "audition_only": True,
    "salvaged_from_postcommit_owner_failure": True,
    "formal_training_completed_claim": False,
    "release_eligible": False,
    "salvage_verification": r"E:\EV\20260808_164409.training\runs\tsubaki-singleclip-fullstyle-v4-freshbase-100step-r1-20260808t1915cst\salvage-verifications\fullstyle-v4-step100-salvage-20260808t1920cst\SALVAGE_VERIFICATION.json",
}
assert audition.TEXTS == [
    "\u522b\u6015\uff0c\u6211\u53ea\u662f\u60f3\u628a\u4f60\u7559\u5728\u8eab\u8fb9\uff0c\u518d\u4e45\u4e00\u70b9\u70b9\u3002",
    "\u4f60\u53ef\u4ee5\u53bb\u4efb\u4f55\u5730\u65b9\u2026\u2026\u53ea\u8981\u6700\u540e\uff0c\u8fd8\u662f\u56de\u5230\u6211\u8fd9\u91cc\u3002",
]
audition.__file__ = str(Path(__file__).resolve())

if __name__ == "__main__":
    raise SystemExit(audition.main())
