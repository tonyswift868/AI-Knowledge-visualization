from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil
from typing import Any, Sequence

from .contracts import FreeChatMessage, FreeChatSession


FreeChatSummarizationHook = Callable[[Sequence[FreeChatMessage]], str | None]


@dataclass(frozen=True, slots=True)
class FreeChatHistorySelection:
    messages: tuple[FreeChatMessage, ...]
    omitted_messages: tuple[FreeChatMessage, ...]
    summary: str | None
    estimated_tokens: int
    budget_exceeded_by_required_context: bool


@dataclass(frozen=True, slots=True)
class FreeChatHistoryWindow:
    """Optional cost boundary for a future integration caller.

    The default keeps the complete session. A configured limit selects recent
    messages without adding summaries, course instructions, or other semantic
    policy.
    """

    max_messages: int | None = None
    max_estimated_tokens: int | None = None
    chars_per_token: int = 4
    summarization_hook: FreeChatSummarizationHook | None = None

    def __post_init__(self) -> None:
        if self.max_messages is not None and self.max_messages < 0:
            raise ValueError("max_messages cannot be negative")
        if self.max_estimated_tokens is not None and self.max_estimated_tokens < 1:
            raise ValueError("max_estimated_tokens must be positive")
        if self.chars_per_token < 1:
            raise ValueError("chars_per_token must be positive")

    def estimate_text_tokens(self, text: str) -> int:
        return max(1, ceil(len(text) / self.chars_per_token))

    def estimate_message_tokens(self, message: FreeChatMessage) -> int:
        return 4 + self.estimate_text_tokens(message.content)

    def select(
        self,
        messages: Sequence[FreeChatMessage],
        *,
        required_texts: Sequence[str] = (),
    ) -> FreeChatHistorySelection:
        complete = tuple(messages)
        if self.max_messages is None:
            candidates = complete
        elif self.max_messages == 0:
            candidates = ()
        else:
            candidates = complete[-self.max_messages :]

        required_tokens = sum(
            4 + self.estimate_text_tokens(text)
            for text in required_texts
            if str(text or "").strip()
        )
        selected_reversed: list[FreeChatMessage] = []
        selected_tokens = 0
        for message in reversed(candidates):
            message_tokens = self.estimate_message_tokens(message)
            if (
                self.max_estimated_tokens is not None
                and required_tokens + selected_tokens + message_tokens
                > self.max_estimated_tokens
            ):
                break
            selected_reversed.append(message)
            selected_tokens += message_tokens

        selected = tuple(reversed(selected_reversed))
        omitted = complete[: len(complete) - len(selected)] if selected else complete
        summary: str | None = None
        if omitted and self.summarization_hook is not None:
            candidate_summary = str(self.summarization_hook(omitted) or "").strip()
            if candidate_summary:
                summary_tokens = 4 + self.estimate_text_tokens(candidate_summary)
                if (
                    self.max_estimated_tokens is None
                    or required_tokens + selected_tokens + summary_tokens
                    <= self.max_estimated_tokens
                ):
                    summary = candidate_summary
                    selected_tokens += summary_tokens

        return FreeChatHistorySelection(
            messages=selected,
            omitted_messages=omitted,
            summary=summary,
            estimated_tokens=required_tokens + selected_tokens,
            budget_exceeded_by_required_context=(
                self.max_estimated_tokens is not None
                and required_tokens > self.max_estimated_tokens
            ),
        )


def build_provider_messages(
    session: FreeChatSession,
    latest_user_message: str,
    *,
    persona_context: str | None = None,
    history_window: FreeChatHistoryWindow | None = None,
) -> list[dict[str, Any]]:
    """Build a plain conversational request with no default system prompt."""

    latest = str(latest_user_message or "").strip()
    if not latest:
        raise ValueError("latest_user_message cannot be empty")

    provider_messages: list[dict[str, Any]] = []
    raw_persona = session.persona_context if persona_context is None else persona_context
    persona = str(raw_persona or "").strip()
    if persona:
        provider_messages.append({"role": "system", "content": persona})

    selected = (history_window or FreeChatHistoryWindow()).select(
        session.messages,
        required_texts=(persona, latest),
    )
    if selected.summary:
        provider_messages.append({"role": "system", "content": selected.summary})
    provider_messages.extend(
        {"role": message.role, "content": message.content}
        for message in selected.messages
    )
    provider_messages.append({"role": "user", "content": latest})
    return provider_messages
