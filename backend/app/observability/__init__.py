"""MentorFlow enterprise observability public surface."""

from .api import install_observability, observe_http_request
from .audit import (
    AuditBoundaryError,
    EnterpriseAuditBoundary,
    JsonlAuditSink,
    validate_audit_envelope,
)
from .context import CorrelationContext, correlation_scope, current_correlation
from .health import configure_default_health_probes, health_registry
from .instrumentation import (
    observe_business_operation,
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
from .recovery import (
    BackupPlan,
    PostgresEndpoint,
    RecoveryOperationError,
    RestorePlan,
    create_backup,
    load_and_verify_manifest,
    restore_backup,
)
from .slo import SloWindow, error_budgets

__all__ = [
    "CorrelationContext",
    "AuditBoundaryError",
    "BackupPlan",
    "EnterpriseAuditBoundary",
    "JsonlAuditSink",
    "PostgresEndpoint",
    "RecoveryOperationError",
    "RestorePlan",
    "SloWindow",
    "configure_default_health_probes",
    "configure_open_telemetry",
    "correlation_scope",
    "create_backup",
    "current_correlation",
    "error_budgets",
    "health_registry",
    "install_observability",
    "observe_http_request",
    "observe_business_operation",
    "observe_job",
    "otel_status",
    "load_and_verify_manifest",
    "record_auth_attempt",
    "record_authorization_denial",
    "record_backup_freshness",
    "record_db_pool",
    "record_document_result",
    "record_job_queued",
    "record_provider_receipt",
    "record_rls_denial",
    "record_teacher_turn",
    "restore_backup",
    "registry",
    "validate_audit_envelope",
]
