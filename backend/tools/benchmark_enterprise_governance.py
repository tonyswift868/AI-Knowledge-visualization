"""Offline process benchmark for E1-03B governance policy paths.

This is deliberately not a server, database-network, or load benchmark.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.enterprise.contracts_v1 import (
    ActorContext,
    ActorKind,
    AuditEnvelope,
    ResourceClassification,
    ResourceScope,
    ResourceScopeType,
    TenantContext,
    TenantMode,
)
from app.governance.audit import SQLiteAuditLog
from app.governance.authorization import AuthorizationService
from app.governance.models import Action, GovernanceActorAdapter, PolicyRequest, ResourceType, Role
from app.governance.runtime import (
    AuthorizedQueryPolicy,
    AuthorizedResourceCandidate,
    ResourceVisibilityFilter,
)


NOW = datetime(2026, 8, 26, 18, 0, tzinfo=timezone.utc)


def fixture() -> tuple[GovernanceActorAdapter, TenantContext, ResourceScope]:
    actor = ActorContext(
        subject_id="benchmark-owner",
        actor_kind=ActorKind.SERVICE,
        issuer="mentorflow.offline.benchmark",
        token_id="benchmark-token-metadata",
        session_id="benchmark-session",
        request_id="benchmark-request",
        authentication_time=NOW,
        authentication_strength="offline-fixture",
        organization_id="benchmark-org",
        workspace_id="benchmark-workspace",
        membership_id="benchmark-membership",
        roles=(Role.OWNER.value,),
    )
    subject = GovernanceActorAdapter(
        actor=actor,
        effective_role=Role.OWNER,
        workspace_memberships=frozenset({"benchmark-workspace"}),
    )
    tenant = TenantContext(
        mode=TenantMode.ENTERPRISE_SERVER,
        organization_id="benchmark-org",
        workspace_id="benchmark-workspace",
    )
    scope = ResourceScope(
        scope_type=ResourceScopeType.WORKSPACE,
        organization_id="benchmark-org",
        workspace_id="benchmark-workspace",
        classification=ResourceClassification.CONFIDENTIAL,
    )
    return subject, tenant, scope


def rate(count: int, elapsed_seconds: float) -> float:
    return round(count / elapsed_seconds, 2) if elapsed_seconds else 0.0


def run(temp_root: Path) -> dict[str, object]:
    temp_root.mkdir(parents=True, exist_ok=True)
    subject, tenant, resource_scope = fixture()
    with tempfile.TemporaryDirectory(prefix="e103b-benchmark-", dir=temp_root) as raw_dir:
        root = Path(raw_dir)

        authorization_log = SQLiteAuditLog(root / "authorization.sqlite3")
        authorization = AuthorizationService(authorization_log)
        policy_request = PolicyRequest(
            subject=subject,
            tenant=tenant,
            action=Action.READ,
            resource=ResourceType.DOCUMENT,
            resource_id="benchmark-document",
            resource_scope=resource_scope,
            trace_id="benchmark-authorization",
            occurred_at=NOW,
        )
        started = perf_counter()
        allowed = sum(1 for _ in range(10_000) if authorization.authorize(policy_request).allowed)
        authorization_seconds = perf_counter() - started
        if allowed != 10_000 or len(authorization_log.records()) != 10_000:
            raise RuntimeError("authorization benchmark cardinality mismatch")
        authorization_log.close()

        append_log = SQLiteAuditLog(root / "audit-append.sqlite3")
        started = perf_counter()
        for index in range(10_000):
            append_log.append(
                AuditEnvelope(
                    event_id="benchmark-audit-" + uuid4().hex,
                    actor_context=subject.actor,
                    action="benchmark.audit.append",
                    resource_type="benchmark",
                    resource_id=f"resource-{index}",
                    outcome="ALLOW",
                    reason_code="OFFLINE_BENCHMARK",
                    occurred_at=NOW,
                    request_id=subject.actor.request_id,
                    trace_id="benchmark-audit-append",
                )
            )
        audit_seconds = perf_counter() - started
        if len(append_log.records()) != 10_000 or not append_log.verify_integrity().valid:
            raise RuntimeError("audit append benchmark integrity mismatch")
        append_log.close()

        bulk_results: dict[str, object] = {}
        for size in (100, 1_000, 10_000):
            bulk_log = SQLiteAuditLog(root / f"bulk-{size}.sqlite3")
            visibility = ResourceVisibilityFilter(AuthorizationService(bulk_log))
            candidates = tuple(
                AuthorizedResourceCandidate(
                    resource_id=f"document-{index}",
                    resource_scope=resource_scope,
                    payload=index,
                )
                for index in range(size)
            )
            started = perf_counter()
            visible = visibility.list(
                AuthorizedQueryPolicy(
                    subject=subject,
                    tenant=tenant,
                    action=Action.READ,
                    resource=ResourceType.DOCUMENT,
                    trace_id=f"benchmark-bulk-{size}",
                    occurred_at=NOW,
                ),
                candidates,
            )
            elapsed = perf_counter() - started
            if len(visible) != size or len(bulk_log.records()) != 1:
                raise RuntimeError(f"bulk benchmark cardinality mismatch for {size}")
            bulk_results[str(size)] = {
                "candidate_count": size,
                "visible_count": len(visible),
                "audit_append_count": 1,
                "elapsed_seconds": round(elapsed, 6),
                "resources_per_second": rate(size, elapsed),
            }
            bulk_log.close()

    return {
        "schema_version": "mentorflow.enterprise-governance-offline-benchmark.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "classification": "OFFLINE_PROCESS_BENCHMARK_NOT_SERVER_LOAD",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "authorization_decisions": {
            "count": 10_000,
            "audit_append_count": 10_000,
            "elapsed_seconds": round(authorization_seconds, 6),
            "decisions_per_second": rate(10_000, authorization_seconds),
        },
        "audit_append": {
            "count": 10_000,
            "integrity_verified": True,
            "elapsed_seconds": round(audit_seconds, 6),
            "appends_per_second": rate(10_000, audit_seconds),
        },
        "bulk_resource_filter": bulk_results,
        "required_fixture_coverage": [100, 1_000, 10_000],
        "provider_calls": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--temp-root",
        type=Path,
        default=Path(r"E:\Codex\Temp\MF-e1-governance\benchmark"),
    )
    args = parser.parse_args()
    print(json.dumps(run(args.temp_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
