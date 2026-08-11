from __future__ import annotations

import copy
import gc
import importlib.util
import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location("mentorflow_chunked_adamw", ROOT / "scripts" / "chunked_adamw.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
ChunkedAdamW = MODULE.ChunkedAdamW


class ChunkedAdamWTests(unittest.TestCase):
    def _equivalence(self, dtype: torch.dtype, atol: float, rtol: float) -> None:
        torch.manual_seed(20260808)
        originals = [torch.randn(97, dtype=dtype), torch.randn(7, 13, dtype=dtype)]
        reference = [torch.nn.Parameter(value.clone()) for value in originals]
        chunked = [torch.nn.Parameter(value.clone()) for value in originals]
        expected = torch.optim.AdamW(reference, lr=2e-4, betas=(0.85, 0.97), eps=1e-6, weight_decay=0.03, foreach=False)
        actual = ChunkedAdamW(chunked, lr=2e-4, betas=(0.85, 0.97), eps=1e-6, weight_decay=0.03, chunk_bytes=1024, test_allow_cpu=True)
        for step in range(5):
            for index, (left, right) in enumerate(zip(reference, chunked)):
                gradient = torch.sin(originals[index].float() * (step + 1)).to(dtype)
                left.grad = gradient.clone()
                right.grad = gradient.clone()
            expected.step(); actual.step()
            expected.zero_grad(set_to_none=True); actual.zero_grad(set_to_none=True)
        for left, right in zip(reference, chunked):
            torch.testing.assert_close(right, left, atol=atol, rtol=rtol)
            left_state, right_state = expected.state[left], actual.state[right]
            torch.testing.assert_close(right_state["exp_avg"], left_state["exp_avg"], atol=atol, rtol=rtol)
            torch.testing.assert_close(right_state["exp_avg_sq"], left_state["exp_avg_sq"], atol=atol, rtol=rtol)
            self.assertEqual(float(right_state["step"]), float(left_state["step"]))
        saved = copy.deepcopy(actual.state_dict())
        reloaded_params = [torch.nn.Parameter(value.detach().clone()) for value in chunked]
        reloaded = ChunkedAdamW(reloaded_params, lr=9e-3, chunk_bytes=1024, test_allow_cpu=True)
        reloaded.load_state_dict(saved)
        for old, new in zip(chunked, reloaded_params):
            gradient = torch.cos(old.detach().float()).to(dtype)
            old.grad = gradient.clone(); new.grad = gradient.clone()
        actual.step(); reloaded.step()
        for old, new in zip(chunked, reloaded_params):
            torch.testing.assert_close(new, old, atol=atol, rtol=rtol)

    def test_cpu_float32_matches_torch_adamw_and_reload(self) -> None:
        self._equivalence(torch.float32, atol=2e-7, rtol=2e-6)

    def test_cpu_bfloat16_matches_torch_adamw_and_reload(self) -> None:
        self._equivalence(torch.bfloat16, atol=8e-3, rtol=8e-3)

    @unittest.skipUnless(torch.cuda.is_available(), "CUDA is required for peak-memory comparison")
    def test_cuda_chunked_update_has_lower_peak_than_torch_adamw(self) -> None:
        def peak(chunked: bool) -> int:
            torch.cuda.empty_cache(); gc.collect(); torch.cuda.reset_peak_memory_stats()
            parameter = torch.nn.Parameter(torch.ones(4_194_304, device="cuda", dtype=torch.bfloat16))
            parameter.grad = torch.full_like(parameter, 0.25)
            if chunked:
                optimizer = ChunkedAdamW([parameter], lr=2e-6, chunk_bytes=1024 * 1024)
            else:
                optimizer = torch.optim.AdamW([parameter], lr=2e-6, foreach=False)
            optimizer.step(); torch.cuda.synchronize()
            value = torch.cuda.max_memory_allocated()
            del optimizer, parameter
            torch.cuda.empty_cache(); gc.collect()
            return value
        standard_peak = peak(False)
        chunked_peak = peak(True)
        self.assertLess(chunked_peak, standard_peak)


if __name__ == "__main__":
    unittest.main()
