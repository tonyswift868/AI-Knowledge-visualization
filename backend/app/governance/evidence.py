from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.enterprise.contracts_v1 import verify_frozen_contract_hashes
from app.governance.audit import SQLiteAuditLog
from app.governance.audit_coverage import AuditCoverageVerifier
from app.governance.authorization import AuthorizationService
from app.governance.e2e_harness import GovernanceE2EHarness
from app.governance.negative_pack import AuthorizationNegativePack
from app.governance.secret_access import SecretAccessVerifier
from app.governance.surface_manifest import ENTERPRISE_PROTECTED_SURFACE_MANIFEST


SCHEMA_VERSION = "mentorflow.enterprise-policy-integration-evidence.v1"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _manifest_payload(generated_at: str) -> dict[str, object]:
    manifest = ENTERPRISE_PROTECTED_SURFACE_MANIFEST
    return {
        "schema_version": manifest.schema_version,
        "evidence_schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "operation_count": len(manifest.operations),
        "domain_count": len({operation.domain for operation in manifest.operations}),
        "audit_requirement_counts": dict(
            sorted(Counter(operation.audit_requirement.value for operation in manifest.operations).items())
        ),
        "operations": [
            {
                "operation_id": operation.operation_id,
                "domain": operation.domain.value,
                "action": operation.action.value,
                "resource_type": operation.resource_type.value,
                "required_policy": operation.required_policy.value,
                "audit_requirement": operation.audit_requirement.value,
                "tenant_scope": operation.tenant_scope.value,
                "transports": sorted(transport.value for transport in operation.transports),
                "source_seams": operation.source_seams,
                "sensitive": operation.sensitive,
            }
            for operation in manifest.operations
        ],
    }


def _staging_qa_payload(generated_at: str) -> dict[str, object]:
    templates = (
        {
            "template_id": "E1-03C-STAGING-OIDC",
            "environment": "OIDC",
            "prerequisites": [
                "E1-02 production context resolver bound to canonical enterprise-context.v1",
                "non-production issuer, clients, and revoked membership fixture",
            ],
            "checks": [
                "role and organization claims are accepted only from validated tokens",
                "revoked membership denies and emits AuditEnvelope",
                "query/header role and tenant forgery is ignored",
            ],
        },
        {
            "template_id": "E1-03C-STAGING-POSTGRES-RLS",
            "environment": "POSTGRESQL_RLS",
            "prerequisites": [
                "Integration-owned PostgreSQL schema, migrations, indexes, and RLS policies",
                "three isolated staging organizations with workspace data",
            ],
            "checks": [
                "application authorization and RLS both deny foreign organization/workspace rows",
                "DENY creates no DB mutation, job, Provider, or object write",
                "Document, Audit, Progress, and Plan export return only authorized tenant rows",
            ],
        },
        {
            "template_id": "E1-03C-STAGING-ADMIN-UI",
            "environment": "ADMIN_UI",
            "prerequisites": [
                "Enterprise Admin feature mounted by Integration",
                "authenticated non-production Owner, Content Manager, and Auditor accounts",
            ],
            "checks": [
                "navigation state matches EnterpriseNavigationModel",
                "hidden or disabled navigation never substitutes for backend authorization",
                "Provider secret plaintext and credential references are absent from rendered output",
            ],
        },
        {
            "template_id": "E1-03C-STAGING-CONTEXT-SWITCHER",
            "environment": "CONTEXT_SWITCHER",
            "prerequisites": [
                "E1-02 trusted organization and workspace switch endpoint",
                "actor with distinct roles in at least two workspaces",
            ],
            "checks": [
                "role and workspace navigation reprojects immediately after validated switch",
                "stale context requests fail closed",
                "foreign workspace selection cannot be forged through query or headers",
            ],
        },
    )
    return {
        "schema_version": "mentorflow.enterprise-staging-qa-pack.v1",
        "generated_at": generated_at,
        "status": "TEMPLATES_ONLY_NOT_PUBLISHED",
        "requires_current_user_action": False,
        "published_requests": 0,
        "live_acceptance_claimed": False,
        "templates": templates,
    }


def generate_policy_evidence(
    output_root: Path,
    *,
    app_root: Path,
    temp_root: Path,
) -> tuple[Path, ...]:
    if not verify_frozen_contract_hashes():
        raise AssertionError("frozen enterprise-context.v1 hashes do not match")
    generated_at = datetime.now(timezone.utc).isoformat()
    output_root.mkdir(parents=True, exist_ok=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    protected_manifest_path = output_root / "protected-surface-manifest.json"
    _write_json(protected_manifest_path, _manifest_payload(generated_at))
    outputs.append(protected_manifest_path)

    with tempfile.TemporaryDirectory(prefix="e103c-evidence-", dir=temp_root) as raw_temp:
        evidence_temp = Path(raw_temp)
        audit = SQLiteAuditLog(evidence_temp / "negative-audit.sqlite3")
        negative_report = AuthorizationNegativePack().run(AuthorizationService(audit))
        if not negative_report.complete:
            raise AssertionError("authorization negative pack is incomplete")
        coverage_verifier = AuditCoverageVerifier()
        coverage_report = coverage_verifier.assert_complete(audit.records())
        matrix = coverage_verifier.matrix()
        audit_integrity = audit.verify_integrity()

        audit_path = output_root / "audit-coverage-report.json"
        _write_json(
            audit_path,
            {
                "schema_version": "mentorflow.enterprise-audit-coverage.v1",
                "generated_at": generated_at,
                "matrix": {
                    "AUDIT_REQUIRED": matrix.audit_required,
                    "AUDIT_OPTIONAL": matrix.audit_optional,
                    "NO_AUDIT": matrix.no_audit,
                },
                "verification": coverage_report.to_public_dict(),
                "source_audit_record_count": len(audit.records()),
                "audit_chain_valid": audit_integrity.valid,
            },
        )
        outputs.append(audit_path)

        negative_path = output_root / "authorization-negative-report.json"
        _write_json(
            negative_path,
            {
                "schema_version": "mentorflow.enterprise-authorization-negative-report.v1",
                "generated_at": generated_at,
                **negative_report.to_public_dict(),
            },
        )
        outputs.append(negative_path)
        audit.close()

        e2e_report = GovernanceE2EHarness().run(evidence_temp / "governance-e2e.sqlite3")
        if not e2e_report.complete:
            raise AssertionError("governance E2E harness is incomplete")
        e2e_path = output_root / "governance-e2e-report.json"
        _write_json(e2e_path, e2e_report.to_public_dict())
        outputs.append(e2e_path)

    secret_report = SecretAccessVerifier().scan(app_root)
    secret_path = output_root / "secret-access-report.json"
    _write_json(
        secret_path,
        {
            "schema_version": "mentorflow.enterprise-secret-access-report.v1",
            "generated_at": generated_at,
            "delivery_blocking": not secret_report.complete,
            "scope_resolution": (
                "ARCHITECT_AUTHORIZATION_REQUIRED_FOR_OUT_OF_OWNERSHIP_FIX"
                if not secret_report.complete
                else "NONE"
            ),
            **secret_report.to_public_dict(),
        },
    )
    outputs.append(secret_path)

    staging_path = output_root / "staging-qa-pack.json"
    _write_json(staging_path, _staging_qa_payload(generated_at))
    outputs.append(staging_path)

    integration_path = output_root / "policy-integration-manifest.json"
    _write_json(
        integration_path,
        {
            "schema_version": "mentorflow.enterprise-policy-integration-manifest.v1",
            "generated_at": generated_at,
            "frozen_contract": {
                "name": "enterprise-context.v1",
                "module": "app.enterprise.contracts_v1",
                "integration_commit": "fa488048d2856d687854b50a23137a45bb020d7d",
                "bundle_sha256": "03e4f66505b6b98202a07a70a39925293876ecc7b7c72f617dbe3b76dcb8b628",
                "hashes_matched": True,
                "parallel_shared_contracts": 0,
            },
            "endpoint_neutral_guard": "app.governance.surface_enforcement.authorize_request",
            "fastapi_adapter": "app.governance.surface_enforcement.FastAPIRequestGuard",
            "service_guard": "app.governance.surface_enforcement.AuthorizedServiceCall",
            "backend_authorization_authoritative": True,
            "central_router_modified": False,
            "provider_calls": 0,
            "real_oidc_calls": 0,
            "real_postgres_calls": 0,
            "live_acceptance_claimed": False,
            "blocking_findings": tuple(
                {
                    "code": violation.code,
                    "path": violation.path,
                    "symbol": violation.symbol,
                    "resolution": "explicit Architect ownership authorization required",
                }
                for violation in secret_report.violations
            ),
        },
    )
    outputs.append(integration_path)
    return tuple(outputs)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    args = parser.parse_args()
    outputs = generate_policy_evidence(
        args.output_root,
        app_root=args.app_root,
        temp_root=args.temp_root,
    )
    print(json.dumps({"generated": [str(path) for path in outputs]}, indent=2))


if __name__ == "__main__":
    main()
