"""Credential-free capability routing for MentorFlow model tasks."""

from .capabilities import (
    Capability,
    CapabilityStatus,
    ProviderCapabilityProfile,
    RoutingMode,
)
from .requirements import (
    DEFAULT_TASK_REQUIREMENTS,
    CapabilityFallback,
    TaskCapabilityRequirement,
    TaskType,
    requirement_for,
)
from .router import (
    CapabilityDegradation,
    CapabilityRouter,
    RoutingDecision,
    RoutingPreference,
    RoutingQualityMetadata,
    route_provider,
)

__all__ = [
    "Capability",
    "CapabilityDegradation",
    "CapabilityFallback",
    "CapabilityRouter",
    "CapabilityStatus",
    "DEFAULT_TASK_REQUIREMENTS",
    "ProviderCapabilityProfile",
    "RoutingDecision",
    "RoutingMode",
    "RoutingPreference",
    "RoutingQualityMetadata",
    "TaskCapabilityRequirement",
    "TaskType",
    "requirement_for",
    "route_provider",
]
