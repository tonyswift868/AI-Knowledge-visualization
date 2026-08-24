"""Independent Free Chat backend foundation.

This package intentionally exposes no HTTP route or persistence integration.
Those boundaries belong to a later integration wave.
"""

from .contracts import FreeChatMessage, FreeChatSession, FreeChatTurnResult
from .prompt import FreeChatHistoryWindow, build_provider_messages
from .service import (
    FreeChatEmptyResponseError,
    FreeChatInputError,
    FreeChatProviderResponseError,
    FreeChatService,
    TextProvider,
)
from .session import (
    FreeChatSessionConflictError,
    FreeChatSessionNotFoundError,
    FreeChatSessionStore,
    InMemoryFreeChatSessionStore,
)

__all__ = [
    "FreeChatEmptyResponseError",
    "FreeChatHistoryWindow",
    "FreeChatInputError",
    "FreeChatMessage",
    "FreeChatProviderResponseError",
    "FreeChatService",
    "FreeChatSession",
    "FreeChatSessionConflictError",
    "FreeChatSessionNotFoundError",
    "FreeChatSessionStore",
    "FreeChatTurnResult",
    "InMemoryFreeChatSessionStore",
    "TextProvider",
    "build_provider_messages",
]
