from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    REASONING = "reasoning"
    VISION = "vision"
    AUDIO = "audio"
    STREAMING = "streaming"
    CONTEXT_WINDOW = "context_window"


class CapabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    AVAILABLE_WITH_DEGRADATION = "AVAILABLE_WITH_DEGRADATION"
    OPTIONAL_ENHANCEMENT_UNAVAILABLE = "OPTIONAL_ENHANCEMENT_UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"


class RoutingMode(str, Enum):
    ECONOMIC = "economic"
    BALANCED = "balanced"
    QUALITY = "quality"


def _routing_mode(value: RoutingMode | str) -> RoutingMode:
    if isinstance(value, RoutingMode):
        return value
    return RoutingMode(str(value).strip().lower())


@dataclass(frozen=True, slots=True)
class ProviderCapabilityProfile:
    """Public, credential-free capabilities for one configured model."""

    provider: str
    model: str
    text_generation: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    reasoning: bool = False
    vision: bool = False
    audio: bool = False
    streaming: bool = False
    context_window: int | None = None
    routing_classes: tuple[RoutingMode, ...] = (RoutingMode.BALANCED,)

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip()
        model = str(self.model or "").strip()
        if not provider:
            raise ValueError("provider must be non-empty")
        if not model:
            raise ValueError("model must be non-empty")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("context_window must be positive when provided")
        routing_classes = tuple(
            sorted(
                {_routing_mode(value) for value in self.routing_classes},
                key=lambda value: value.value,
            )
        )
        if not routing_classes:
            routing_classes = (RoutingMode.BALANCED,)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "routing_classes", routing_classes)

    @property
    def capabilities(self) -> frozenset[Capability]:
        values = {
            capability
            for capability, enabled in (
                (Capability.TEXT_GENERATION, self.text_generation),
                (Capability.STRUCTURED_OUTPUT, self.structured_output),
                (Capability.TOOL_CALLING, self.tool_calling),
                (Capability.REASONING, self.reasoning),
                (Capability.VISION, self.vision),
                (Capability.AUDIO, self.audio),
                (Capability.STREAMING, self.streaming),
                (Capability.CONTEXT_WINDOW, self.context_window is not None),
            )
            if enabled
        }
        return frozenset(values)

    def supports(self, capability: Capability | str) -> bool:
        normalized = (
            capability if isinstance(capability, Capability) else Capability(capability)
        )
        return normalized in self.capabilities
