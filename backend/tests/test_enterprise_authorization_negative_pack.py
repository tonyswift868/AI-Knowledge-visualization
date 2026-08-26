from __future__ import annotations

from app.governance.audit import SQLiteAuditLog
from app.governance.authorization import AuthorizationService
from app.governance.negative_pack import AuthorizationNegativePack


def test_negative_pack_covers_all_operations_domains_scenarios_and_transports(tmp_path) -> None:
    audit = SQLiteAuditLog(tmp_path / "negative-pack.sqlite3")
    report = AuthorizationNegativePack().run(AuthorizationService(audit))
    assert report.complete is True
    assert len(report.cases) == 404
    assert report.scenario_counts == {
        "FOREIGN_ORGANIZATION": 78,
        "FOREIGN_WORKSPACE": 78,
        "FORGED_IDENTIFIER": 78,
        "OWN_RESOURCE": 78,
        "REVOKED_MEMBERSHIP": 78,
        "WRONG_ROLE": 14,
    }
    assert sum(case.actual_allowed for case in report.cases) == 78
    assert sum(not case.actual_allowed for case in report.cases) == 326
    assert {"GET", "POST", "PATCH", "DELETE", "EXPORT"}.issubset(
        report.transport_counts
    )
    assert len({case.domain for case in report.cases}) == 14
    assert all(case.transport_claims_ignored for case in report.cases)
    assert len(audit.records()) == 404


def test_negative_pack_preserves_specific_fail_closed_reasons(tmp_path) -> None:
    audit = SQLiteAuditLog(tmp_path / "negative-reasons.sqlite3")
    report = AuthorizationNegativePack().run(AuthorizationService(audit))
    reasons = {case.scenario: case.reason_code for case in report.cases}
    assert reasons["FOREIGN_ORGANIZATION"] == "RESOURCE_ORGANIZATION_MISMATCH"
    assert reasons["FOREIGN_WORKSPACE"] == "RESOURCE_WORKSPACE_MISMATCH"
    assert reasons["FORGED_IDENTIFIER"] == "RESOURCE_NOT_FOUND"
    assert reasons["REVOKED_MEMBERSHIP"] == "ACTOR_INACTIVE"
    assert reasons["WRONG_ROLE"] == "ROLE_MATRIX_DENY"
    assert report.failures == ()
