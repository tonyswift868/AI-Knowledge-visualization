from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .contracts import FreeChatMessage, FreeChatSession


@dataclass(frozen=True, slots=True)
class FreeChatHistoryWindow:
    """Optional cost boundary for a future integration caller.

    The default keeps the complete session. A configured limit selects recent
    messages without adding summaries, course instructions, or other semantic
    policy.
    """

    max_messages: int | None = None

    def __post_init__(self) -> None:
        if self.max_messages is not None and self.max_messages < 0:
            raise ValueError("max_messages cannot be negative")

    def select(self, messages: Sequence[FreeChatMessage]) -> Sequence[FreeChatMessage]:
        if self.max_messages is None:
            return messages
        if self.max_messages == 0:
            return ()
        return messages[-self.max_messages :]


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
    persona = str(persona_context or "").strip()
    if persona:
        provider_messages.append({"role": "system", "content": persona})

    selected = (history_window or FreeChatHistoryWindow()).select(session.messages)
    provider_messages.extend(
        {"role": message.role, "content": message.content}
        for message in selected
    )
    provider_messages.append({"role": "user", "content": latest})
    return provider_messages
