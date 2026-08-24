"""Independent Free Chat backend foundation.

This package intentionally exposes no HTTP route. Its runtime, DTOs, and
storage remain application-owned and transport-neutral.
"""

from .contracts import (
    FreeChatMessage,
    FreeChatRole,
    FreeChatSession,
    FreeChatTurnResult,
)
from .dto import (
    CreateFreeChatSessionDTO,
    FreeChatMessageDTO,
    FreeChatSessionDTO,
    FreeChatTurnDTO,
    SendFreeChatMessageDTO,
)
from .persistence import (
    AtomicJsonFreeChatStore,
    FreeChatStoreCorruptionError,
    FreeChatStoreError,
    FreeChatStoreIOError,
)
from .prompt import (
    FreeChatHistorySelection,
    FreeChatHistoryWindow,
    FreeChatSummarizationHook,
    build_provider_messages,
)
from .runtime import FreeChatRuntime
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
from .streaming import FreeChatChunk, StreamResult

__all__ = [
    "AtomicJsonFreeChatStore",
    "CreateFreeChatSessionDTO",
    "FreeChatChunk",
    "FreeChatEmptyResponseError",
    "FreeChatHistorySelection",
    "FreeChatHistoryWindow",
    "FreeChatInputError",
    "FreeChatMessage",
    "FreeChatMessageDTO",
    "FreeChatProviderResponseError",
    "FreeChatRuntime",
    "FreeChatRole",
    "FreeChatService",
    "FreeChatSession",
    "FreeChatSessionConflictError",
    "FreeChatSessionDTO",
    "FreeChatSessionNotFoundError",
    "FreeChatSessionStore",
    "FreeChatStoreCorruptionError",
    "FreeChatStoreError",
    "FreeChatStoreIOError",
    "FreeChatSummarizationHook",
    "FreeChatTurnDTO",
    "FreeChatTurnResult",
    "InMemoryFreeChatSessionStore",
    "SendFreeChatMessageDTO",
    "StreamResult",
    "TextProvider",
    "build_provider_messages",
]
