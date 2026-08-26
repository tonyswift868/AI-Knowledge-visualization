from __future__ import annotations

from app.governance.e2e_harness import GovernanceE2EHarness


def test_deterministic_e2e_harness_covers_three_tenants_roles_and_resources(tmp_path) -> None:
    report = GovernanceE2EHarness().run(tmp_path / "governance-e2e.sqlite3")
    assert report.complete is True
    assert report.organization_count == 3
    assert report.workspace_count == 3
    assert report.role_count == 3
    assert report.cohort_count == 3
    assert report.resource_count == 3
    assert report.audit_record_count == 8
    assert report.audit_chain_valid is True
    assert report.provider_calls == 0
    assert report.real_oidc_calls == 0
    assert report.real_postgres_calls == 0


def test_e2e_cross_tenant_deny_has_zero_side_effects_and_two_audits(tmp_path) -> None:
    report = GovernanceE2EHarness().run(tmp_path / "governance-e2e-deny.sqlite3")
    denied = next(
        scenario
        for scenario in report.scenarios
        if scenario.scenario_id == "cross-tenant-document-deny"
    )
    assert denied.api_allowed is False
    assert denied.service_allowed is False
    assert denied.reason_code == "RESOURCE_ORGANIZATION_MISMATCH"
    assert denied.side_effect_count == 0
    assert denied.audit_records_added == 2
    assert denied.forged_transport_claims_ignored is True
    assert denied.passed is True
