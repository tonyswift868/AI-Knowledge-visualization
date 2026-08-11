from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import torch
from safetensors.torch import load_file, save_file


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "streaming_safetensors.py"
SPEC = importlib.util.spec_from_file_location("mentorflow_streaming_safetensors", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
STREAM = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STREAM)


def fixture() -> tuple[dict[str, torch.Tensor], torch.Tensor]:
    torch.manual_seed(17)
    state = {
        "speaker_encoder.drop_me": torch.arange(4, dtype=torch.float32),
        "talker.model.codec_embedding.weight": torch.randn(3002, 8, dtype=torch.bfloat16),
        "talker.projection.bias": torch.randn(11, dtype=torch.float32),
        "talker.projection.weight": torch.randn(11, 8, dtype=torch.float32),
    }
    target = torch.randn(1, 8, dtype=torch.float32)
    return state, target


class StreamingSafetensorsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="mentorflow-streaming-safe-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def test_semantics_match_safetensors_and_codec_slot_is_rewritten(self) -> None:
        state, target = fixture()
        output = self.root / "streamed.safetensors"
        receipt = STREAM.write_streaming_safetensors(
            state,
            output,
            target_speaker_embedding=target,
            chunk_bytes=127,
        )
        verification = STREAM.verify_streaming_safetensors(output, receipt, expected_speaker_embedding=target)
        loaded = load_file(str(output))
        expected = {name: tensor.clone() for name, tensor in state.items() if not name.startswith("speaker_encoder")}
        expected["talker.model.codec_embedding.weight"][3000] = target[0].to(torch.bfloat16)
        conventional = self.root / "conventional.safetensors"
        save_file(expected, str(conventional), metadata={"format": "pt"})
        conventional_loaded = load_file(str(conventional))
        self.assertEqual(sorted(loaded), sorted(conventional_loaded))
        for name in loaded:
            self.assertTrue(torch.equal(loaded[name], conventional_loaded[name]), name)
        self.assertNotIn("speaker_encoder.drop_me", loaded)
        self.assertEqual(verification["status"], "verified")
        self.assertTrue(verification["all_tensor_payload_hashes_verified"])

    def test_truncation_tamper_and_overwrite_are_rejected(self) -> None:
        state, target = fixture()
        output = self.root / "model.safetensors"
        receipt = STREAM.write_streaming_safetensors(state, output, target_speaker_embedding=target, chunk_bytes=113)
        original = output.read_bytes()
        with self.assertRaises(STREAM.StreamingSafetensorsError):
            STREAM.write_streaming_safetensors(state, output, target_speaker_embedding=target)
        self.assertEqual(output.read_bytes(), original)
        tampered = self.root / "tampered.safetensors"
        tampered.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with self.assertRaises(STREAM.StreamingSafetensorsError):
            STREAM.verify_streaming_safetensors(tampered, receipt, expected_speaker_embedding=target)
        truncated = self.root / "truncated.safetensors"
        truncated.write_bytes(original[:-9])
        with self.assertRaises(STREAM.StreamingSafetensorsError):
            STREAM.verify_streaming_safetensors(truncated, receipt, expected_speaker_embedding=target)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA unavailable")
    def test_cuda_tensors_use_streaming_path_and_load_exact(self) -> None:
        state, target = fixture()
        cuda_state = {name: tensor.cuda() for name, tensor in state.items()}
        cuda_target = target.cuda()
        output = self.root / "cuda.safetensors"
        receipt = STREAM.write_streaming_safetensors(
            cuda_state,
            output,
            target_speaker_embedding=cuda_target,
            chunk_bytes=257,
        )
        STREAM.verify_streaming_safetensors(output, receipt, expected_speaker_embedding=target)
        loaded = load_file(str(output))
        self.assertTrue(torch.equal(loaded["talker.model.codec_embedding.weight"][3000], target[0].to(torch.bfloat16)))

    def test_large_tensor_writer_has_bounded_working_set_delta(self) -> None:
        try:
            import psutil
        except ImportError:
            self.skipTest("psutil unavailable")
        # 96 MiB payload: the writer must not create another full CPU state
        # dict.  Allow 32 MiB for allocator noise around a 4 MiB chunk.
        state, target = fixture()
        state["talker.large.weight"] = torch.zeros(24 * 1024 * 1024, dtype=torch.float32)
        process = psutil.Process(os.getpid())
        baseline = process.memory_info().rss
        maximum = baseline
        stop = False

        def sample() -> None:
            nonlocal maximum
            while not stop:
                maximum = max(maximum, process.memory_info().rss)
                time.sleep(0.005)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            receipt = STREAM.write_streaming_safetensors(
                state,
                self.root / "large.safetensors",
                target_speaker_embedding=target,
                chunk_bytes=4 * 1024 * 1024,
            )
        finally:
            stop = True
            sampler.join(timeout=2)
        large_receipt = next(item for item in receipt["tensors"] if item["name"] == "talker.large.weight")
        self.assertEqual(large_receipt["bytes"], 96 * 1024 * 1024)
        self.assertLess(maximum - baseline, 36 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
