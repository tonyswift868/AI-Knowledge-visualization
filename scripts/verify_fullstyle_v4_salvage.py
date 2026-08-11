#!/usr/bin/env python3
"""Create-new verification for a fully committed v4 checkpoint after owner cleanup failure."""

from __future__ import annotations
import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import qwen3_tts_sft_safe as safe

RUN = Path(r"E:\EV\20260808_164409.training\runs\tsubaki-singleclip-fullstyle-v4-freshbase-100step-r1-20260808t1915cst")
CHECKPOINT = RUN / "checkpoints" / "checkpoint-step-00000100"
PLAN_SHA = "3f5ca52db6a33c12640940a9b8c4f6a6a46fe681d43f0afb9323b43da621b54d"

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def write_new(path: Path, value: object) -> None:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    with path.open("xb") as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())

def main() -> int:
    owner = json.loads((RUN / "OWNER_RECEIPT.json").read_text(encoding="utf-8"))
    step = json.loads((RUN / "attempts" / "attempt-0001" / "EVAL_RESOURCE_STEP_00000100.json").read_text(encoding="utf-8"))
    plan = json.loads((RUN / "RUN_PLAN.json").read_text(encoding="utf-8"))
    if owner.get("status") != "failed" or "below 2.5 GiB" not in str(owner.get("violation")):
        raise RuntimeError("expected post-commit owner RAM violation is absent")
    if step.get("optimizer_step") != 100 or step.get("loss_and_grad_finite") is not True:
        raise RuntimeError("step100 finite evidence is absent")
    if plan.get("plan_sha256") != PLAN_SHA or plan.get("fullstyle_v4") is not True or plan["training"].get("max_optimizer_steps") != 100:
        raise RuntimeError("v4 plan identity mismatch")
    if "warm_start" in plan or "parent_checkpoint" in plan:
        raise RuntimeError("fresh Base plan contains a parent")
    receipt = safe._verify_checkpoint(CHECKPOINT, PLAN_SHA)
    state = receipt["trainer_state"]
    writer = state["checkpoint_writer"]
    verification = writer["model_tensor_verification"]
    optimizer = CHECKPOINT / "accelerator" / "optimizer.bin"
    random_state = CHECKPOINT / "accelerator" / "random_states_0.pkl"
    accel_model = CHECKPOINT / "accelerator" / "model.safetensors"
    if receipt.get("global_optimizer_step") != 100 or state.get("warm_start") is not None:
        raise RuntimeError("checkpoint step/parent mismatch")
    if state.get("accelerator_state_written") is not True or state.get("resume_eligible") is not True:
        raise RuntimeError("optimizer state contract missing")
    if not all(p.is_file() and p.stat().st_size > 0 for p in (optimizer, random_state, accel_model)):
        raise RuntimeError("accelerator optimizer/random/model state incomplete")
    if verification.get("status") != "verified" or verification.get("tensor_count") != 402 or verification.get("all_tensor_payload_hashes_verified") is not True or verification.get("speaker_slot_verified") is not True:
        raise RuntimeError("streaming tensor verification incomplete")
    output = RUN / "salvage-verifications" / "fullstyle-v4-step100-salvage-20260808t1920cst"
    output.mkdir(parents=True, exist_ok=False)
    result = {
        "schema": "mentorflow.single-clip-fullstyle-v4-salvage-verification.v1",
        "status": "passed_audition_only_nonrelease",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "checkpoint": str(CHECKPOINT),
        "checkpoint_receipt_sha256": sha(CHECKPOINT / "CHECKPOINT_RECEIPT.json"),
        "checkpoint_tree_sha256": receipt["tree_sha256"],
        "model_safetensors_sha256": sha(CHECKPOINT / "model" / "model.safetensors"),
        "model_tensor_count": 402,
        "all_model_tensor_hashes_verified": True,
        "speaker_slot_verified": True,
        "global_optimizer_step": 100,
        "step100_loss": step["loss"],
        "step100_loss_finite": True,
        "optimizer_state": {"path": str(optimizer), "bytes": optimizer.stat().st_size, "sha256": sha(optimizer)},
        "random_state": {"path": str(random_state), "bytes": random_state.stat().st_size, "sha256": sha(random_state)},
        "single_continuous_optimizer_run": True,
        "fresh_base": True,
        "warm_start": None,
        "owner_post_commit_cleanup_failed": True,
        "owner_violation": owner["violation"],
        "checkpoint_committed_before_violation": True,
        "scope": "private_local_nonrelease_audition_only",
        "release_eligible": False,
        "network_used": False,
    }
    write_new(output / "SALVAGE_VERIFICATION.json", result)
    print(json.dumps({**result, "verification_path": str(output / "SALVAGE_VERIFICATION.json")}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
