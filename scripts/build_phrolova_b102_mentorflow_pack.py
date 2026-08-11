"""Build the verified Phrolova B1.02 candidate as a MentorFlow pack.

This wrapper deliberately reuses the existing B-only pack builder while
keeping the old 1.0.1 pack intact.  It creates a new local signing key entry
for the 1.0.2 technical candidate; activation remains a separate operation.
"""

from __future__ import annotations

import sys
from pathlib import Path


SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import build_phrolova_b_reference_variant as builder  # noqa: E402


builder.VERSION = "1.0.2"
builder.SIGNING_KEY_ID = "mentorflow-local-gpt-sovits-b102-20260811"


def main() -> None:
    root = Path(r"E:\AI_Tools\GPTSoVITS")
    phrolova = root / "Phrolova"
    result = builder.build_variant(
        catalog_path=root / "voice_catalog" / "teacher_voice_pack_catalog.v1.json",
        catalog_root=root / "voice_catalog",
        gpt_path=(
            phrolova
            / "work_b102_20260810"
            / "run_v2"
            / "training"
            / "gpt_weights"
            / "phrolova_b102_fresh-e5.ckpt"
        ),
        sovits_path=(
            phrolova
            / "work_combined_v2"
            / "training"
            / "sovits_weights"
            / "phrolova_combined_v2_fresh_e8_s216.pth"
        ),
        reference_path=(
            Path(r"E:\AI_Tools\Phrolova_TTS_Benchmark")
            / "timbre_refs"
            / "TIMBRE_REF_A.wav"
        ),
        transcript="不必心急，我也很好奇，在尽头等待着他们的，是怎样的答案。",
    )
    print(result)


if __name__ == "__main__":
    main()
