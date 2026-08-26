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
    assert payload["policy"]["authority_guard"] == "INTEGRATION_AUTHORITY_GUARD.json"
    assert payload["policy"]["worker_new_task_assignment_authority"] == "ARCHITECT_ONLY"
    assert payload["policy"]["a_direct_new_feature_dispatch"] == "DISABLED"
    assert payload["policy"]["a_integration_review_feedback"] == "ENABLED"
    assert payload["policy"]["a_return_to_owner_repair_guidance"] == "ENABLED"
    assert payload["policy"]["a_periodic_worker_chat"] == "DISABLED"
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
    assert by_id["E1-03"]["state"] in {
        "INTEGRATED_COMPLETION_EXACT_COMMITS_FOCUSED_GATES_PENDING",
        "INTEGRATED_COMPLETION_EXACT_COMMITS_FOCUSED_GATES_PASSED",
    }
    assert by_id["E1-03"]["delivery_id"] == "DLV-W03-20260826T111731Z-03"
    assert by_id["E1-03"]["source_commits"] == [
        "cf0125f1faba87094031f8eba2d51cfd3d03778a",
        "2af634f1fa81575be63e4fa42635b454a57c2c2b",
    ]
    assert by_id["E1-03"]["completion_delivery_id"] == "DLV-W03-20260826T124718Z-04"
    assert by_id["E1-03"]["completion_delivery_receipt_sha256"] == "357113BE370340F05BC3B539CA1389899CA207B24241AA3FE0EF41F9F93295A4"
    assert by_id["E1-03"]["completion_source_commits"] == [
        "ad02d2a743d4012d54aaad0404a9b98a6d4a78dc",
        "bd50e6e9a618310900b3483657490b43c5913f45",
        "411f06188db7bbf291fccee5d888eac48f91237c",
    ]
    assert by_id["E1-03"]["completion_integration_commits"] == [
        "fd95ff57ea8bbf8f3161e10da86fb4a79c790896",
        "af4df71ad98cad1c33b223cbd519334ded8073e6",
        "0dfcc82f4bad7f6ca776055b5599ffef19fe07fd",
    ]
    assert by_id["E1-03"]["supersedes_returned_delivery"] == "DLV-W03-20260826T110330Z-02"
    assert by_id["E1-04"]["state"] == "READY_PENDING_BLOCKING_QA_CHECKOUT_PREPARED"
    assert by_id["E1-04"]["delivery_id"] == "DLV-W04-20260826T111010Z-01"
    assert by_id["E1-04"]["qa_request"] == "TRQ-W04-20260826-195459-02"
    assert by_id["E1-04"]["qa_request_sha256"] == "D800C0960DB3FFAAB3E57FFF21045AF06862DC8CAF7D8827176DE2701A279A5B"
    assert by_id["E1-04"]["qa_checkout_head"] == "52cc4546329e175d853a7cf09b7506f20c9d9f29"
    assert by_id["E1-04"]["qa_checkout_attestation_sha256"] == "D9BDED7AD5E85AB86635D2B47D71663AB91DCC04B60B33FF5954850F257397E3"
    assert "No E1-04 feature commit has been applied to canonical Integration" in by_id["E1-04"]["notes"]
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
    assert by_id["E1-07"]["state"] == "READY_AUDITED_CHAIN_COMPLETE_BLOCKED_BY_E1_04_QA"
    assert by_id["E1-07"]["delivery_id"] == "DLV-W07-20260826T105638Z-01"
    assert by_id["E1-07"]["source_commits"] == [
        "4011ea382f19509ce0a26591a5fa2617dff13dcb",
        "11d134cecb7c338e1b1e9a65195a3be0bc1d8e69",
    ]
    assert by_id["E1-07"]["incremental_delivery_id"] == "DLV-W07-20260826T125249Z-02"
    assert by_id["E1-07"]["incremental_delivery_receipt_sha256"] == "FA9682EDE0F2BFF41DC321062A1F8C654AC5D832C3F82C2D22E821181371E0A4"
    assert by_id["E1-07"]["incremental_source_commits"] == [
        "7b889bd269d9b44f7e3d22117e388ac9430ca6a2",
        "f85bc7b8e398827fecf0c4deef4ac63e41fd3284",
    ]
    assert "Neither delivery has been applied" in by_id["E1-07"]["notes"]


def test_integration_authority_guard_keeps_new_tasks_with_architect_and_review_feedback_with_a() -> None:
    payload = _read("INTEGRATION_AUTHORITY_GUARD.json")
    authority = payload["authority"]
    assert authority["WORKER_NEW_TASK_ASSIGNMENT_AUTHORITY"] == "ARCHITECT_ONLY"
    assert authority["A_DIRECT_NEW_FEATURE_DISPATCH"] == "DISABLED"
    assert authority["A_INTEGRATION_REVIEW_FEEDBACK"] == "ENABLED"
    assert authority["A_RETURN_TO_OWNER_REPAIR_GUIDANCE"] == "ENABLED"
    assert authority["A_PERIODIC_WORKER_CHAT"] == "DISABLED"
    assert authority["NO_PERIODIC_AGENT_CHAT"] is True
    assert authority["NO_WORKER_OFFICE_MUTATION_BY_A"] is True

    feedback = payload["review_feedback"]
    assert feedback["allowed_only_after_actual_delivery_review"] is True
    assert feedback["one_way_precise_feedback_allowed"] is True
    assert feedback["periodic_or_status_chat_forbidden"] is True
    assert feedback["review_after_fix"] == "REQUIRED"
    assert set(feedback["required_fields"]) == {
        "DELIVERY_ID",
        "WORKER_ID",
        "AUDIT_RESULT",
        "WHY",
        "FIX_SCOPE",
        "FILES",
        "TESTS",
        "DO_NOT_TOUCH",
        "REVIEW_AFTER_FIX",
    }

    assert payload["return_to_owner"]["minimal_fix_scope_only"] is True
    assert payload["return_to_owner"]["scope_expansion_forbidden"] is True
    assert payload["out_of_scope_improvement_disposition"] == "ARCHITECT_FOLLOWUP_SUGGESTION_ONLY"
    assert payload["merge_behavior"]["worker_message_required_on_pass"] is False


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
