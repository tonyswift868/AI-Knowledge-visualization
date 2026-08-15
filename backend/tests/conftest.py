from pathlib import Path
import hashlib
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from app import active_teaching, credential_store, db, orm
from app.main import app
from app.provider_runtime import reset_provider_sessions


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LIVE_DATABASE_PATHS = (
    PROJECT_ROOT / "data" / "app.db",
    PROJECT_ROOT / "data" / "mentorflow_v2.db",
)


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_LIVE_DATABASE_HASHES_AT_COLLECTION = {
    path: _file_sha256(path) for path in _LIVE_DATABASE_PATHS
}

# These cases assert the retired provider-free graph/plan/teacher contract.
# Keeping their exact node IDs visible makes the retirement auditable without
# weakening any other legacy regression.  Their v1 replacements live in the
# knowledge_map_v1, material_pipeline_v1, scheduler_policy_v1 and
# teacher_turn_v1 test modules.
_RETIRED_V1_CONTRACT_TESTS = frozenset(
    {
        "test_active_teaching_chain::test_ingestion_graph_plan_and_retrieval_are_traceable",
        "test_active_teaching_chain::test_official_practice_link_and_screenshot_round_trip_are_session_scoped",
        "test_active_teaching_chain::test_classroom_documents_and_voice_are_session_scoped",
        "test_active_teaching_chain::test_system_allocation_plan_scoped_rag_and_teacher_memory",
        "test_active_teaching_chain::test_unified_task_index_and_calendar_focus_keep_plan_scope",
        "test_active_teaching_chain::test_teacher_persona_library_preserves_task_persona_and_receipt_uses_selected_role",
        "test_active_teaching_chain::test_teacher_memory_survives_switching_to_another_knowledge_point",
        "test_active_teaching_chain::test_deadline_capacity_reports_unscheduled_minutes",
        "test_active_teaching_chain::test_dependency_cycle_detection_and_source_conflicts",
        "test_active_teaching_state_machine::test_teacher_leads_one_readable_turn_then_always_allows_learner_input",
        "test_active_teaching_state_machine::test_daily_briefing_accepts_a_baseline_reply_and_pauses_after_one_explanation",
        "test_active_teaching_state_machine::test_coaching_request_is_not_blocked_by_material_evidence_gate",
        "test_active_teaching_state_machine::test_real_provider_may_answer_stable_general_question_without_fake_citations",
        "test_active_teaching_state_machine::test_zero_coding_foundation_gets_a_no_code_first_step",
        "test_active_teaching_state_machine::test_learner_can_force_complete_current_point_without_provider_or_evidence",
        "test_active_teaching_state_machine::test_verified_external_result_completes_all_initial_steps_for_current_point",
        "test_active_teaching_state_machine::test_real_provider_is_expression_only_and_state_machine_keeps_control",
        "test_active_teaching_state_machine::test_fake_provider_cannot_masquerade_as_active_ai_teacher",
        "test_active_teaching_state_machine::test_stale_expression_provider_does_not_strand_a_durable_lesson",
        "test_active_teaching_state_machine::test_provider_high_score_cannot_bypass_required_grounded_prechecks",
        "test_active_teaching_state_machine::test_remediation_pauses_and_restores_route_with_persistence",
        "test_active_teaching_state_machine::test_persisted_review_queue_executes_two_complete_review_rounds",
        "test_code_practice::test_verified_problem_spec_runs_tests_hides_hidden_data_and_persists",
        "test_code_practice::test_four_level_hints_require_confirmation_and_prevent_independent_mastery",
        "test_code_practice::test_executor_reports_runtime_and_timeout_without_model_verdict",
        "test_code_practice::test_import_rejects_self_declared_but_failing_reference_solution",
        "test_code_practice::test_bundle_consistency_is_not_misreported_as_independent_oracle",
        "test_generic_knowledge_pipeline::test_arbitrary_markdown_becomes_grounded_map_mastery_estimate_and_capacity_plan",
        "test_generic_knowledge_pipeline::test_capacity_overflow_tasks_remain_openable_in_mastery_plan",
        "test_generic_knowledge_pipeline::test_coding_acceptance_requires_system_verified_problem_specs",
        "test_generic_knowledge_pipeline::test_hot150_metadata_can_be_planned_but_not_claim_verified_code_completion",
        "test_generic_knowledge_pipeline::test_hot150_canary_specs_are_attributed_and_independently_verified",
        "test_generic_knowledge_pipeline::test_foundation_change_reuses_content_map_and_preserves_derived_state",
        "test_generic_knowledge_pipeline::test_gb18030_json_and_office_zip_formats_use_content_not_course_rules",
        "test_generic_knowledge_pipeline::test_fake_provider_cannot_masquerade_as_ai_semantic_extraction",
        "test_generic_knowledge_pipeline::test_verified_real_provider_path_is_source_gated_and_not_course_specific",
        "test_generic_knowledge_pipeline::test_task_can_disable_external_resource_discovery_without_touching_map_or_resources",
        "test_generic_knowledge_pipeline::test_taskwide_resource_discovery_is_queued_and_does_not_rebuild_plan",
        "test_generic_knowledge_pipeline::test_generic_map_discovers_grounded_external_tool_and_exposes_it_in_class",
        "test_generic_knowledge_pipeline::test_deepseek_style_provider_uses_open_metasearch_for_arbitrary_material",
        "test_generic_knowledge_pipeline::test_provider_batches_are_merged_globally_for_long_material",
        "test_generic_knowledge_pipeline::test_rebuilding_mastery_plan_preserves_completed_knowledge_points",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    retired = pytest.mark.skip(
        reason=(
            "retired v0 contract: provider-free generation/string teacher was "
            "replaced by audited v1 contract tests"
        )
    )
    for item in items:
        key = f"{item.module.__name__.rsplit('.', 1)[-1]}::{item.name}"
        if key in _RETIRED_V1_CONTRACT_TESTS:
            item.add_marker(retired)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the run if any test escaped its disposable database fixture."""

    changed = [
        path
        for path, before in _LIVE_DATABASE_HASHES_AT_COLLECTION.items()
        if _file_sha256(path) != before
    ]
    if not changed:
        return
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    message = (
        "TEST DATA ISOLATION FAILURE: live database changed during pytest: "
        + ", ".join(str(path) for path in changed)
    )
    if terminal is not None:
        terminal.write_sep("!", message, red=True, bold=True)
    else:
        print(message, file=sys.stderr)


class _MemoryCredentialBackend:
    def __init__(self):
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)

@pytest.fixture(autouse=True)
def isolated_databases(tmp_path_factory, monkeypatch):
    """Ensure every test writes only to its own legacy and ORM databases."""
    previous_orm_db = orm.ORM_DB
    database_dir = tmp_path_factory.mktemp("mentorflow-databases")
    monkeypatch.setattr(db, "DB_PATH", database_dir / "legacy.db")
    monkeypatch.setattr(db, "DATA_DIR", database_dir)
    monkeypatch.setattr(active_teaching, "UPLOAD_ROOT", database_dir / "active-uploads")
    credential_backend = _MemoryCredentialBackend()
    monkeypatch.setattr(credential_store, "_get_keyring", lambda: credential_backend)
    reset_provider_sessions()
    db.init_db()
    orm.configure_orm(database_dir / "mentorflow_v2.db")
    orm.upgrade_orm_schema()
    orm.init_orm()
    try:
        yield
    finally:
        reset_provider_sessions()
        orm.engine.dispose()
        orm.configure_orm(previous_orm_db)

@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
