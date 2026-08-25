from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import ProviderUsageMetadata, safe_identifier


@dataclass(frozen=True, slots=True)
class AdapterExecutionResult:
    success: bool
    output_text: str | None = None
    structured_output: dict[str, object] | None = None
    error_code: str | None = None
    retryable: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def usage(self) -> ProviderUsageMetadata:
        return ProviderUsageMetadata(
            prompt_tokens=max(0, int(self.prompt_tokens)),
            completion_tokens=max(0, int(self.completion_tokens)),
            total_tokens=max(0, int(self.prompt_tokens))
            + max(0, int(self.completion_tokens)),
            attempt_count=1,
            fake_provider_calls=1,
            real_provider_calls=0,
        )


class ProviderAdapterPort(Protocol):
    port_name: str
    is_fake: bool

    async def execute(
        self,
        *,
        task_type: str,
        payload: str,
        expect_structured: bool,
        native_structured: bool,
    ) -> AdapterExecutionResult: ...


class FakeProviderAdapter:
    is_fake = True

    def __init__(
        self,
        *,
        port_name: str,
        script: tuple[AdapterExecutionResult, ...] = (),
    ) -> None:
        self.port_name = safe_identifier(port_name, field_name="port_name")
        self._script = tuple(script)
        self.call_count = 0

    async def execute(
        self,
        *,
        task_type: str,
        payload: str,
        expect_structured: bool,
        native_structured: bool,
    ) -> AdapterExecutionResult:
        index = self.call_count
        self.call_count += 1
        if self._script:
            return self._script[min(index, len(self._script) - 1)]
        prompt_tokens = max(1, len(payload.split()))
        if expect_structured and native_structured:
            return AdapterExecutionResult(
                success=True,
                output_text="Fake Provider structured response.",
                structured_output={"status": "fake", "task_type": task_type},
                prompt_tokens=prompt_tokens,
                completion_tokens=6,
            )
        if expect_structured:
            return AdapterExecutionResult(
                success=True,
                output_text=(
                    '{"status":"fake","transport":"prompted_json","task_type":"'
                    + task_type
                    + '"}'
                ),
                prompt_tokens=prompt_tokens,
                completion_tokens=8,
            )
        return AdapterExecutionResult(
            success=True,
            output_text="Fake Provider execution completed.",
            prompt_tokens=prompt_tokens,
            completion_tokens=5,
        )
