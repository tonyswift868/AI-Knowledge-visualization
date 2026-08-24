"""Credential-free capability routing for MentorFlow model tasks."""

from .capabilities import (
    Capability,
    CapabilityStatus,
    ProviderCapabilityProfile,
    RoutingMode,
)
from .identity import (
    BaseURLClass,
    CanonicalProviderIdentity,
    ProviderProfileRegistration,
    ProviderProfileSource,
    capability_profile_id,
    classify_base_url,
)
from .orchestrator import (
    ProviderOrchestrator,
    RouteDecisionReceipt,
    RoutingQualityPolicy,
    TaskRoutingDiagnostic,
    TaskRoutingMatrix,
)
from .registry import ProviderProfileRegistry, RegisteredProviderProfile
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
    "BaseURLClass",
    "Capability",
    "CapabilityDegradation",
    "CapabilityFallback",
    "CapabilityRouter",
    "CapabilityStatus",
    "CanonicalProviderIdentity",
    "DEFAULT_TASK_REQUIREMENTS",
    "ProviderCapabilityProfile",
    "ProviderOrchestrator",
    "ProviderProfileRegistration",
    "ProviderProfileRegistry",
    "ProviderProfileSource",
    "RegisteredProviderProfile",
    "RouteDecisionReceipt",
    "RoutingDecision",
    "RoutingMode",
    "RoutingPreference",
    "RoutingQualityPolicy",
    "RoutingQualityMetadata",
    "TaskCapabilityRequirement",
    "TaskRoutingDiagnostic",
    "TaskRoutingMatrix",
    "TaskType",
    "capability_profile_id",
    "classify_base_url",
    "requirement_for",
    "route_provider",
]
