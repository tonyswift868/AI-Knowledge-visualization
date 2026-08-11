#!/usr/bin/env python3
"""Mathematically equivalent dense AdamW with bounded elementwise temporaries.

Only the elementwise ``sqrt/div/add/addcdiv`` update is chunked. First and
second moments, bias correction, decoupled weight decay, and serialized state
retain the standard :class:`torch.optim.AdamW` contract.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import torch
from torch.optim import Optimizer


DEFAULT_CHUNK_BYTES = 32 * 1024 * 1024


class ChunkedAdamW(Optimizer):
    def __init__(
        self,
        params: Iterable[torch.Tensor],
        lr: float = 1e-3,
        betas: tuple[float, float] = (0.9, 0.999),
        eps: float = 1e-8,
        weight_decay: float = 1e-2,
        *,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        foreach: bool = False,
        fused: bool = False,
        amsgrad: bool = False,
        capturable: bool = False,
        differentiable: bool = False,
        test_allow_cpu: bool = False,
    ) -> None:
        if lr < 0.0 or eps < 0.0 or weight_decay < 0.0:
            raise ValueError("lr, eps, and weight_decay must be non-negative")
        if not 0.0 <= betas[0] < 1.0 or not 0.0 <= betas[1] < 1.0:
            raise ValueError("betas must lie in [0, 1)")
        if type(chunk_bytes) is not int or not 1024 <= chunk_bytes <= 64 * 1024 * 1024:
            raise ValueError("chunk_bytes must be an integer in [1024, 64 MiB]")
        if any((foreach, fused, amsgrad, capturable, differentiable)):
            raise ValueError("foreach/fused/amsgrad/capturable/differentiable are unsupported and fail closed")
        defaults = dict(
            lr=lr,
            betas=betas,
            eps=eps,
            weight_decay=weight_decay,
            foreach=False,
            fused=False,
            amsgrad=False,
            capturable=False,
            differentiable=False,
            maximize=False,
        )
        super().__init__(params, defaults)
        self.chunk_bytes = chunk_bytes
        self.test_allow_cpu = bool(test_allow_cpu)

    def _validate(self, parameter: torch.Tensor, gradient: torch.Tensor) -> None:
        if gradient.is_sparse:
            raise RuntimeError("ChunkedAdamW rejects sparse gradients")
        if not parameter.is_contiguous() or not gradient.is_contiguous():
            raise RuntimeError("ChunkedAdamW requires contiguous parameters and gradients")
        if parameter.dtype not in (torch.bfloat16, torch.float32):
            raise RuntimeError("ChunkedAdamW supports BF16 production and float32 equivalence tests only")
        if gradient.dtype != parameter.dtype or gradient.device != parameter.device:
            raise RuntimeError("gradient dtype/device must match its parameter")
        if parameter.device.type != "cuda" and not self.test_allow_cpu:
            raise RuntimeError("production ChunkedAdamW requires CUDA parameters")
        if parameter.device.type == "cuda" and parameter.dtype != torch.bfloat16:
            raise RuntimeError("production CUDA ChunkedAdamW requires BF16 parameters")

    @torch.no_grad()
    def step(self, closure: Any = None) -> Any:
        loss = None
        if closure is not None:
            with torch.enable_grad():
                loss = closure()
        for group in self.param_groups:
            beta1, beta2 = group["betas"]
            for parameter in group["params"]:
                gradient = parameter.grad
                if gradient is None:
                    continue
                self._validate(parameter, gradient)
                state = self.state[parameter]
                if not state:
                    state["step"] = torch.tensor(0.0, dtype=torch.float32)
                    state["exp_avg"] = torch.zeros_like(parameter, memory_format=torch.preserve_format)
                    state["exp_avg_sq"] = torch.zeros_like(parameter, memory_format=torch.preserve_format)
                step_tensor = state["step"]
                exp_avg = state["exp_avg"]
                exp_avg_sq = state["exp_avg_sq"]
                if step_tensor.device.type != "cpu" or step_tensor.dtype != torch.float32:
                    raise RuntimeError("ChunkedAdamW state step must be a CPU float32 scalar")
                if exp_avg.shape != parameter.shape or exp_avg_sq.shape != parameter.shape or exp_avg.dtype != parameter.dtype or exp_avg_sq.dtype != parameter.dtype:
                    raise RuntimeError("ChunkedAdamW state is incompatible with its parameter")
                step_tensor.add_(1)
                step = float(step_tensor.item())
                parameter.mul_(1.0 - group["lr"] * group["weight_decay"])
                exp_avg.lerp_(gradient, 1.0 - beta1)
                exp_avg_sq.mul_(beta2).addcmul_(gradient, gradient, value=1.0 - beta2)
                bias_correction1 = 1.0 - beta1**step
                bias_correction2_sqrt = math.sqrt(1.0 - beta2**step)
                step_size = group["lr"] / bias_correction1
                p_flat = parameter.view(-1)
                avg_flat = exp_avg.view(-1)
                avg_sq_flat = exp_avg_sq.view(-1)
                elements = max(1, self.chunk_bytes // parameter.element_size())
                for start in range(0, parameter.numel(), elements):
                    stop = min(parameter.numel(), start + elements)
                    denom = avg_sq_flat[start:stop].sqrt()
                    denom.div_(bias_correction2_sqrt).add_(group["eps"])
                    p_flat[start:stop].addcdiv_(avg_flat[start:stop], denom, value=-step_size)
                    del denom
        return loss

