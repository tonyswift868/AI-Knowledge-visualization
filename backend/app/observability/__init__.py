"""MentorFlow enterprise observability public surface."""

from .api import install_observability, observe_http_request
from .context import CorrelationContext, correlation_scope, current_correlation
from .health import configure_default_health_probes, health_registry
from .instrumentation import (
    observe_job,
    record_auth_attempt,
    record_authorization_denial,
    record_backup_freshness,
    record_db_pool,
    record_document_result,
    record_job_queued,
    record_provider_receipt,
    record_rls_denial,
    record_teacher_turn,
)
from .metrics import registry
from .runtime import configure_open_telemetry, otel_status
from .slo import error_budgets

__all__ = [
    "CorrelationContext",
    "configure_default_health_probes",
    "configure_open_telemetry",
    "correlation_scope",
    "current_correlation",
    "error_budgets",
    "health_registry",
    "install_observability",
    "observe_http_request",
    "observe_job",
    "otel_status",
    "record_auth_attempt",
    "record_authorization_denial",
    "record_backup_freshness",
    "record_db_pool",
    "record_document_result",
    "record_job_queued",
    "record_provider_receipt",
    "record_rls_denial",
    "record_teacher_turn",
    "registry",
]
