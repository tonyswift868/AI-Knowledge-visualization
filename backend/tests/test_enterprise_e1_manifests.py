from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
ENTERPRISE_ROOT = ROOT / "enterprise_e1"


def _read(name: str) -> dict:
    return json.loads((ENTERPRISE_ROOT / name).read_text(encoding="utf-8"))


def test_dependency_evaluation_prefers_existing_stack_and_requires_leases() -> None:
    payload = _read("DEPENDENCY_EVALUATION.json")
    evaluations = {entry["capability"]: entry for entry in payload["evaluations"]}
    assert evaluations["ORM"]["decision"] == "REUSE_EXISTING"
    assert evaluations["MIGRATION_TOOL"]["decision"] == "REUSE_EXISTING"
    assert evaluations["POSTGRESQL_DRIVER"]["current_state"] == "ABSENT"
    assert evaluations["POSTGRESQL_DRIVER"]["dependency_lease_required"] is True
    assert evaluations["EXTERNAL_ENTERPRISE_SERVICES"]["decision"] == "KEEP_AS_EXTERNAL_CONTAINERS"
    assert payload["policy"]["stable_business_code_rewrite_for_framework_adoption"] is False


def test_dependency_leases_are_unique_and_fail_closed() -> None:
    payload = _read("DEPENDENCY_LEASES.json")
    capabilities = [entry["capability"] for entry in payload["leases"]]
    assert len(capabilities) == len(set(capabilities))
    assert all(entry["status"] == "NOT_ACQUIRED" for entry in payload["leases"])
    assert all(entry["mutation_allowed"] is False for entry in payload["leases"])


def test_integration_queue_is_dependency_ordered_and_exact_commit_only() -> None:
    payload = _read("INTEGRATION_QUEUE.json")
    queue = payload["queue"]
    assert [entry["work_item"] for entry in queue] == [
        "E1-02",
        "E1-01",
        "E1-03",
        "E1-05",
        "E1-04",
        "E1-06",
        "E1-07",
    ]
    assert payload["policy"]["delivery_fence_only"] is True
    assert payload["policy"]["exact_commits_only"] is True
    assert payload["policy"]["branch_tip_merge_forbidden"] is True
    by_id = {entry["work_item"]: entry for entry in queue}
    assert by_id["E1-01"]["state"] == "INTEGRATED_EXACT_COMMIT_FOCUSED_GATES_PASSED"
    assert by_id["E1-01"]["delivery_id"] == "DLV-W01-20260826T110453Z-05"
    assert by_id["E1-01"]["source_commit"] == "30c7e44d8f92fdc049971e537339925d73887598"
    assert by_id["E1-01"]["integration_commit"] == "296b6592ece7450d478c0c86fd5636eafb6663bc"
    assert "DLV-W01-20260826T091553Z-04 remains unconsumed" in by_id["E1-01"]["notes"]
    assert by_id["E1-02"]["state"] == "INTEGRATED_EXACT_COMMIT_FOCUSED_GATES_PASSED"
    assert by_id["E1-02"]["delivery_id"] == "DLV-W02-20260826T105314Z-02"
    assert by_id["E1-02"]["source_commit"] == "b846ad3c5817f432fb5c815ebe0a66048cb5c065"
    assert by_id["E1-02"]["integration_commit"] == "2207d8867cedfe89a1d9be1768d16662da86abb9"
    assert by_id["E1-03"]["state"] == "INTEGRATED_REPAIR_EXACT_COMMITS_FOCUSED_GATES_PASSED"
    assert by_id["E1-03"]["delivery_id"] == "DLV-W03-20260826T111731Z-03"
    assert by_id["E1-03"]["source_commits"] == [
        "cf0125f1faba87094031f8eba2d51cfd3d03778a",
        "2af634f1fa81575be63e4fa42635b454a57c2c2b",
    ]
    assert by_id["E1-03"]["supersedes_returned_delivery"] == "DLV-W03-20260826T110330Z-02"
    assert by_id["E1-04"]["state"] == "READY_PENDING_BLOCKING_QA"
    assert by_id["E1-04"]["delivery_id"] == "DLV-W04-20260826T111010Z-01"
    assert "No E1-04 feature commit has been applied" in by_id["E1-04"]["notes"]
    assert by_id["E1-05"]["state"] in {
        "INTEGRATED_REPAIR_EXACT_COMMIT_FOCUSED_GATES_PENDING",
        "INTEGRATED_REPAIR_EXACT_COMMIT_FOCUSED_GATES_PASSED",
    }
    assert by_id["E1-05"]["delivery_id"] == "DLV-W05-20260826T113308Z-02"
    assert by_id["E1-05"]["source_commit"] == "258200f84e9a0f4996b3715e62abaf007bd818ba"
    assert by_id["E1-05"]["supersedes_returned_delivery"] == "DLV-W05-20260826T111031Z-01"
    assert by_id["E1-06"]["state"] in {
        "INTEGRATED_EXACT_COMMITS_FOCUSED_GATES_PENDING",
        "INTEGRATED_EXACT_COMMITS_FOCUSED_GATES_PASSED",
    }
    assert by_id["E1-06"]["delivery_id"] == "DLV-W06-20260826T110116Z-01"
    assert by_id["E1-06"]["source_commits"] == [
        "fb7bd515d0ad5d4ad35977da8cea0d61d436fb04",
        "ae2f9876ce876c3c24cf907020dc850fa2bb2c51",
    ]
    assert by_id["E1-07"]["state"] == "IN_PROGRESS_DIRTY_NO_DELIVERY"


def test_migration_ownership_names_are_unique_and_match_e1_02_objects() -> None:
    payload = _read("MIGRATION_OWNERSHIP.json")
    resources = [
        (entry["kind"], entry["name"])
        for owner in payload["owners"]
        for entry in owner["resources"]
    ]
    assert len(resources) == len(set(resources))

    by_owner = {entry["owner"]: entry for entry in payload["owners"]}
    e1_02 = by_owner["E1-02"]
    assert "default schema" in e1_02["implementation_notes"]
    assert "does not create" in e1_02["implementation_notes"]
    actual = {(entry["kind"], entry["name"]): entry for entry in e1_02["resources"]}
    assert ("migration", "0029_enterprise_tenancy_rls") in actual
    assert ("database_role", "mentorflow_app") in actual

    tables = {
        "organizations",
        "organization_memberships",
        "workspaces",
        "workspace_role_bindings",
        "resource_scopes",
        "documents",
        "evidence",
        "knowledge_maps",
        "plans",
        "teacher_sessions",
        "review_campaigns",
        "student_events",
        "cost_events",
    }
    assert {
        name for (kind, name), entry in actual.items()
        if kind == "table" and entry["status"] == "IMPLEMENTED_BY_0029"
    } == tables
    assert {
        name.removeprefix("tenant_isolation_")
        for (kind, name), entry in actual.items()
        if kind == "policy" and entry["status"] == "IMPLEMENTED_BY_0029"
    } == tables
    assert not any(
        name in {"enterprise_identity", "enterprise_tenant"}
        for _, name in actual
    )

    e1_05 = by_owner["E1-05"]
    assert "PERSONAL_LOCAL SQLite" in e1_05["implementation_notes"]
    e1_05_actual = {
        (entry["kind"], entry["name"]): entry for entry in e1_05["resources"]
    }
    assert e1_05_actual[("migration", "0030_enterprise_jobs_v1")]["status"] == "IMPLEMENTED"
    assert {
        name
        for (kind, name), entry in e1_05_actual.items()
        if kind == "table" and entry["status"] == "IMPLEMENTED_BY_0030"
    } == {
        "enterprise_jobs.jobs",
        "enterprise_jobs.outbox",
        "enterprise_jobs.dead_letters",
    }
