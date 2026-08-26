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
    assert by_id["E1-01"]["state"] == "PENDING_TEACHER_V3_QA_AND_REBIND"
    assert by_id["E1-02"]["state"] == "IN_PROGRESS_NO_DELIVERY"
    assert by_id["E1-04"]["state"] == "BLOCKED_WORKTREE_DRIFT"
    assert by_id["E1-05"]["state"] == "OFFICE_STALE_NOT_STARTED"
