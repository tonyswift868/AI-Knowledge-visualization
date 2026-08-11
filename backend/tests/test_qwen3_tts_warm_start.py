from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

import torch
from safetensors.torch import save_file


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("mentorflow_safe_trainer_warm", ROOT / "scripts" / "qwen3_tts_sft_safe.py")
assert SPEC is not None and SPEC.loader is not None
TRAINER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRAINER)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixtureModel(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.talker = torch.nn.Linear(3, 2, bias=True)
        self.speaker_encoder = torch.nn.Linear(3, 2, bias=False)


class WarmStartLoaderTests(unittest.TestCase):
    def test_loads_every_non_speaker_tensor_and_declares_optimizer_reset(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mentorflow-warm-start-") as value:
            root = Path(value)
            source = FixtureModel()
            target = FixtureModel()
            source_state = source.state_dict()
            expected = {name: tensor.clone() for name, tensor in source_state.items() if not name.startswith("speaker_encoder")}
            model_path = root / "model.safetensors"
            save_file(expected, str(model_path))
            promotion = root / "promotion.json"
            promotion.write_bytes(b'{"status":"approved"}\n')
            speaker_before = target.speaker_encoder.weight.detach().clone()
            policy = {
                "continuation_kind": "model_weights_only_warm_start",
                "exact_resume": False,
                "optimizer_state_reset": True,
                "promotion_receipt": str(promotion),
                "promotion_receipt_sha256": sha(promotion),
                "parent_checkpoint_receipt_sha256": "a" * 64,
                "parent_checkpoint_tree_sha256": "b" * 64,
                "parent_global_optimizer_step": 20,
                "model_path": str(model_path),
                "model_bytes": model_path.stat().st_size,
                "model_sha256": sha(model_path),
                "model_tensor_count": len(expected),
            }
            evidence = TRAINER._load_model_only_warm_start(target, policy)
            for name, tensor in expected.items():
                self.assertTrue(torch.equal(target.state_dict()[name], tensor), name)
            self.assertTrue(torch.equal(target.speaker_encoder.weight, speaker_before))
            self.assertEqual(evidence["tensors_loaded_and_schema_verified"], len(expected))
            self.assertTrue(evidence["optimizer_state_reset"])
            self.assertFalse(evidence["exact_resume"])
            self.assertTrue(evidence["loaded_from_parent_checkpoint_not_base"])


if __name__ == "__main__":
    unittest.main()
