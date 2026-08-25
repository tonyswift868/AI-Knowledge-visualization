from __future__ import annotations

from dataclasses import dataclass

CONTROL_CENTER_DELIVERY_ID = "mentorflow-live-demo-03-cost-cache-control-center-v1"
CONTROL_CENTER_PREREQUISITE_COMMIT = "15c4b9ae05c1c529a7a3c668e4c51d8aae8e6750"


@dataclass(frozen=True, slots=True)
class ControlCenterIntegrationManifest:
    delivery_id: str = CONTROL_CENTER_DELIVERY_ID
    prerequisite_commit: str = CONTROL_CENTER_PREREQUISITE_COMMIT
    api_endpoint: str = "/api/cost-cache-control-center"
    frontend_tool_route: str = "?tool=cost-cache"
    durable_execution_env: str = "MENTORFLOW_DURABLE_EXECUTION_DB"
    cache_metrics_env: str = "MENTORFLOW_CACHE_METRICS_DB"
    live_requires_explicit_databases: bool = True
    demo_requires_explicit_query: bool = True
    demo_real_provider_calls: int = 0
    budget_execution_authority: bool = False
    combined_cache_hit_rate: bool = False
    exposed_secret_fields: tuple[str, ...] = ()

    def to_public_dict(self) -> dict[str, object]:
        return {
            "delivery_id": self.delivery_id,
            "prerequisite_commit": self.prerequisite_commit,
            "api_endpoint": self.api_endpoint,
            "frontend_tool_route": self.frontend_tool_route,
            "required_environment": {
                "durable_execution": self.durable_execution_env,
                "cache_metrics": self.cache_metrics_env,
            },
            "live_requires_explicit_databases": (self.live_requires_explicit_databases),
            "demo_requires_explicit_query": self.demo_requires_explicit_query,
            "demo_real_provider_calls": self.demo_real_provider_calls,
            "budget_execution_authority": self.budget_execution_authority,
            "combined_cache_hit_rate": self.combined_cache_hit_rate,
            "exposed_secret_fields": list(self.exposed_secret_fields),
        }


CONTROL_CENTER_INTEGRATION_MANIFEST = ControlCenterIntegrationManifest()


@dataclass(frozen=True, slots=True)
class ControlCenterDeliveryEvidence:
    read_service_verified: bool
    cache_layer_count: int
    combined_hit_percent: str | None
    budget_execution_authority: bool
    restart_verified: bool
    secret_scan_passed: bool
    demo_marked: bool
    demo_real_provider_calls: int
    frontend_mapping_verified: bool


@dataclass(frozen=True, slots=True)
class ControlCenterDeliveryReceipt:
    delivery_id: str
    ready: bool
    failures: tuple[str, ...]
    prerequisite_commit: str
    automatic_execution_policy_change: bool = False

    def to_public_dict(self) -> dict[str, object]:
        return {
            "delivery_id": self.delivery_id,
            "ready": self.ready,
            "failures": list(self.failures),
            "prerequisite_commit": self.prerequisite_commit,
            "automatic_execution_policy_change": (
                self.automatic_execution_policy_change
            ),
        }


def evaluate_control_center_delivery_fence(
    evidence: ControlCenterDeliveryEvidence,
) -> ControlCenterDeliveryReceipt:
    if not isinstance(evidence, ControlCenterDeliveryEvidence):
        raise TypeError("evidence must be ControlCenterDeliveryEvidence")
    checks = (
        (evidence.read_service_verified, "READ_SERVICE_NOT_VERIFIED"),
        (evidence.cache_layer_count == 6, "CACHE_LAYER_COUNT_NOT_SIX"),
        (evidence.combined_hit_percent is None, "CACHE_HIT_RATES_MERGED"),
        (
            not evidence.budget_execution_authority,
            "BUDGET_UI_HAS_EXECUTION_AUTHORITY",
        ),
        (evidence.restart_verified, "RESTART_NOT_VERIFIED"),
        (evidence.secret_scan_passed, "SECRET_SCAN_NOT_PASSED"),
        (evidence.demo_marked, "DEMO_NOT_EXPLICIT"),
        (evidence.demo_real_provider_calls == 0, "DEMO_PROVIDER_CALL_DETECTED"),
        (evidence.frontend_mapping_verified, "FRONTEND_MAPPING_NOT_VERIFIED"),
    )
    failures = tuple(message for passed, message in checks if not passed)
    return ControlCenterDeliveryReceipt(
        delivery_id=CONTROL_CENTER_DELIVERY_ID,
        ready=not failures,
        failures=failures,
        prerequisite_commit=CONTROL_CENTER_PREREQUISITE_COMMIT,
    )
