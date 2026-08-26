"""Offline policy-overhead benchmark for Enterprise governance hot paths.

This is deliberately not a server, database-network, or load benchmark.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from math import ceil
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


def percentile(samples: list[float], percentage: int) -> float:
    if not samples:
        raise ValueError("percentile requires at least one sample")
    if not 0 < percentage <= 100:
        raise ValueError("percentage must be in 1..100")
    ordered = sorted(samples)
    return ordered[max(0, ceil(len(ordered) * percentage / 100) - 1)]


def latency_summary(samples: list[float], *, scale: float = 1_000_000) -> dict[str, float | int]:
    return {
        "sample_count": len(samples),
        "p50_microseconds": round(percentile(samples, 50) * scale, 3),
        "p95_microseconds": round(percentile(samples, 95) * scale, 3),
    }


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
        authorization_samples: list[float] = []
        allowed = 0
        started = perf_counter()
        for _ in range(10_000):
            sample_started = perf_counter()
            decision = authorization.authorize(policy_request)
            authorization_samples.append(perf_counter() - sample_started)
            allowed += int(decision.allowed)
        authorization_seconds = perf_counter() - started
        if allowed != 10_000 or len(authorization_log.records()) != 10_000:
            raise RuntimeError("authorization benchmark cardinality mismatch")
        authorization_log.close()

        append_log = SQLiteAuditLog(root / "audit-append.sqlite3")
        audit_samples: list[float] = []
        started = perf_counter()
        for index in range(10_000):
            sample_started = perf_counter()
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
            audit_samples.append(perf_counter() - sample_started)
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

        bulk_hot_log = SQLiteAuditLog(root / "bulk-hot-path.sqlite3")
        bulk_hot_visibility = ResourceVisibilityFilter(AuthorizationService(bulk_hot_log))
        bulk_hot_candidates = tuple(
            AuthorizedResourceCandidate(
                resource_id=f"hot-document-{index}",
                resource_scope=resource_scope,
                payload=index,
            )
            for index in range(100)
        )
        bulk_batch_samples: list[float] = []
        for batch in range(100):
            sample_started = perf_counter()
            visible = bulk_hot_visibility.list(
                AuthorizedQueryPolicy(
                    subject=subject,
                    tenant=tenant,
                    action=Action.READ,
                    resource=ResourceType.DOCUMENT,
                    trace_id=f"benchmark-bulk-hot-{batch}",
                    occurred_at=NOW,
                ),
                bulk_hot_candidates,
            )
            bulk_batch_samples.append(perf_counter() - sample_started)
            if len(visible) != 100:
                raise RuntimeError("bulk hot-path cardinality mismatch")
        if len(bulk_hot_log.records()) != 100:
            raise RuntimeError("bulk hot-path audit cardinality mismatch")
        bulk_hot_log.close()

    return {
        "schema_version": "mentorflow.enterprise-policy-overhead.v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "classification": "OFFLINE POLICY OVERHEAD",
        "server_latency_claimed": False,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "authorization_decisions": {
            "count": 10_000,
            "audit_append_count": 10_000,
            "elapsed_seconds": round(authorization_seconds, 6),
            "decisions_per_second": rate(10_000, authorization_seconds),
            "local_policy_overhead": latency_summary(authorization_samples),
        },
        "audit_append": {
            "count": 10_000,
            "integrity_verified": True,
            "elapsed_seconds": round(audit_seconds, 6),
            "appends_per_second": rate(10_000, audit_seconds),
            "local_policy_overhead": latency_summary(audit_samples),
        },
        "bulk_resource_filter": bulk_results,
        "bulk_hot_path": {
            "candidate_count": 10_000,
            "batch_count": 100,
            "resources_per_batch": 100,
            "audit_append_count": 100,
            "batch_latency": latency_summary(bulk_batch_samples),
            "per_resource_p50_microseconds": round(
                percentile(bulk_batch_samples, 50) * 10_000,
                3,
            ),
            "per_resource_p95_microseconds": round(
                percentile(bulk_batch_samples, 95) * 10_000,
                3,
            ),
        },
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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.dumps(run(args.temp_root), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
