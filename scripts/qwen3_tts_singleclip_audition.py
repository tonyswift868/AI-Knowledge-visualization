#!/usr/bin/env python3
"""Offline audition wrapper bound only to the accepted single-clip r3 checkpoint."""

from __future__ import annotations

import argparse
import os
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
    "同学你好，我们先从最关键的一步开始。",
    "别着急，我会陪你把这个问题慢慢讲清楚。",
]
audition.TEXT = audition.TEXTS[0]
audition.GENERATION_PREFIX = "tsubaki-singleclip-r3-offline-audition"
assert audition.TEXTS == [
    "\u540c\u5b66\u4f60\u597d\uff0c\u6211\u4eec\u5148\u4ece\u6700\u5173\u952e\u7684\u4e00\u6b65\u5f00\u59cb\u3002",
    "\u522b\u7740\u6025\uff0c\u6211\u4f1a\u966a\u4f60\u628a\u8fd9\u4e2a\u95ee\u9898\u6162\u6162\u8bb2\u6e05\u695a\u3002",
]
# Ensure the owned worker relaunches this binding wrapper, not the historical generic script.
audition.__file__ = str(Path(__file__).resolve())


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--text")
    known, remaining = parser.parse_known_args()
    if known.text:
        os.environ["MENTORFLOW_SINGLECLIP_AUDITION_TEXT"] = known.text
        audition.TEXT = known.text
        audition.TEXTS = [known.text]
    sys.argv = [sys.argv[0], *remaining]
    return audition.main()


if __name__ == "__main__":
    raise SystemExit(main())
