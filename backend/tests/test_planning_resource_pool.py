from __future__ import annotations

import concurrent.futures
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.planning_resource_pool import PlanningPoolError, PlanningResourcePool
from app import orm


def _candidate(index: int, *, url: str | None = None, direct: bool = False) -> dict[str, object]:
    return {
        "url": url or f"https://materials.example/{index}",
        "title": f"Material {index}",
        "stable_id": f"provider-{index}",
        "direct_user_reference": direct,
    }


def _strict_candidate(index: int, *, direct: bool = False) -> dict[str, object]:
    return {
        **_candidate(index, direct=direct),
        "planning_batch_contract": "complete-page-v1",
    }


def test_strict_pool_counts_provided_links_inside_complete_pages():
    pool = PlanningResourcePool()
    created = pool.upsert(
        "pool-strict-pages",
        [_strict_candidate(0, direct=True), *[_strict_candidate(index) for index in range(1, 6)]],
        page_size=3,
    )
    assert created["protected_count"] == 0
    assert len(created["provided_items"]) == 1
    assert created["provided_items"][0]["direct_user_reference"] is True
    assert created["items"][0]["direct_user_reference"] is True
    assert created["qualified_candidate_count"] == 6
    assert created["total_count"] == 6
    assert created["held_incomplete_count"] == 0
    assert created["direct_reference_count"] == 1
    assert created["new_candidate_count"] == 5
    assert len(created["items"]) == 3
    assert created["next_cursor"]


@pytest.mark.parametrize(("direct_count", "fresh_count", "published"), [(1, 2, 3), (3, 3, 6), (4, 2, 6)])
def test_strict_pool_fills_three_card_pages_with_direct_and_fresh_results(
    direct_count: int, fresh_count: int, published: int
):
    pool = PlanningResourcePool()
    direct = [_strict_candidate(index, direct=True) for index in range(direct_count)]
    fresh = [_strict_candidate(direct_count + index) for index in range(fresh_count)]
    created = pool.upsert(
        f"pool-strict-pages-{direct_count}-{fresh_count}",
        [*direct, *fresh],
        page_size=3,
    )
    assert created["total_count"] == published
    assert created["held_incomplete_count"] == 0
    assert created["direct_reference_count"] == direct_count
    assert created["new_candidate_count"] == fresh_count
    all_items = list(created["items"])
    cursor = created.get("next_cursor")
    while cursor:
        page = pool.page(
            f"pool-strict-pages-{direct_count}-{fresh_count}",
            created["planning_session_id"],
            page_size=3,
            cursor=cursor,
        )
        all_items.extend(page["items"])
        cursor = page.get("next_cursor")
    assert len(all_items) == published
    assert sum(bool(item["direct_user_reference"]) for item in all_items) == direct_count
    if direct_count == 4:
        assert sum(bool(item["direct_user_reference"]) for item in all_items[3:6]) == 1


def test_pool_pages_expose_forward_and_previous_cursor_contract():
    pool = PlanningResourcePool()
    first = pool.upsert("pool-pages", [_candidate(i) for i in range(5)], page_size=2)
    session_id = first["planning_session_id"]

    page_one = pool.page("pool-pages", session_id, page_size=2)
    assert page_one["page"] == page_one["page_number"] == 1
    assert page_one["page_index"] == 0
    assert page_one["offset"] == 0
    assert page_one["current_cursor"] is None
    assert page_one["previous_cursor"] is None
    assert page_one["server_paged"] is True
    assert page_one["mode"] == "server_paged"
    assert page_one["page_capacity"] == 6
    assert page_one["page_count"] == 3
    assert page_one["next_cursor"]

    page_two = pool.page("pool-pages", session_id, page_size=2, cursor=page_one["next_cursor"])
    assert page_two["page"] == page_two["page_number"] == 2
    assert page_two["page_index"] == 1
    assert page_two["offset"] == 2
    assert page_two["current_cursor"] == page_one["next_cursor"]
    assert page_two["previous_cursor"]

    page_one_again = pool.page("pool-pages", session_id, page_size=2, cursor=page_two["previous_cursor"])
    assert [item["stable_id"] for item in page_one_again["items"]] == [item["stable_id"] for item in page_one["items"]]


def test_capacity_slots_count_selected_and_exact_references_across_pages():
    pool = PlanningResourcePool()
    created = pool.upsert(
        "pool-capacity",
        [_candidate(0, direct=True), _candidate(1, direct=True), *[_candidate(i) for i in range(2, 6)]],
        page_size=3,
    )
    sid = created["planning_session_id"]
    assert created["page_capacity"] == 6
    assert created["protected_count"] == 2
    assert created["slots_free"] == 4

    current = created
    for candidate in created["items"][:2]:
        current = pool.select(
            "pool-capacity",
            planning_session_id=sid,
            candidate_ids=candidate["candidate_id"],
            expected_revision=current["revision"],
        )
    # Selecting exact references does not double-count them.
    assert current["protected_count"] == 2
    assert current["slots_free"] == 4

    second = pool.page("pool-capacity", sid, page_size=3, cursor=current["next_cursor"])
    for candidate in second["items"][:2]:
        current = pool.select(
            "pool-capacity",
            planning_session_id=sid,
            candidate_ids=candidate["candidate_id"],
            expected_revision=current["revision"],
        )
    assert current["protected_count"] == 4
    assert current["slots_free"] == 2


@pytest.mark.parametrize("initial_count, selected_count", [(2, 1), (4, 2)])
def test_pool_fixture_shapes_keep_selected_rows_across_replenish(initial_count: int, selected_count: int):
    pool = PlanningResourcePool()
    created = pool.upsert(
        f"pool-shape-{initial_count}-{selected_count}",
        [_candidate(index, url=f"https://fixtures.example/{initial_count}/{index}") for index in range(initial_count)],
        page_size=4,
    )
    sid = created["planning_session_id"]
    ids = [item["candidate_id"] for item in created["items"]]
    current = created
    for candidate_id in ids[:selected_count]:
        current = pool.select(
            f"pool-shape-{initial_count}-{selected_count}",
            planning_session_id=sid,
            candidate_ids=candidate_id,
            expected_revision=current["revision"],
        )
    replenished = pool.upsert(
        f"pool-shape-{initial_count}-{selected_count}",
        [_candidate(initial_count + index, url=f"https://fixtures.example/{initial_count}/new-{index}") for index in range(2)],
        planning_session_id=sid,
        mode="replace_unselected",
        expected_revision=current["revision"],
    )
    assert replenished["selected_count"] == selected_count
    assert set(replenished["selection"]["selected"]) >= set(ids[:selected_count])


def test_cursor_is_stale_after_revision_change_and_restart_keeps_state():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-restart", [_candidate(index) for index in range(3)], page_size=2)
    sid = created["planning_session_id"]
    first_page = pool.page("pool-restart", sid, page_size=2)
    cursor = first_page["next_cursor"]
    pool.select("pool-restart", planning_session_id=sid, candidate_ids=first_page["items"][0]["candidate_id"], expected_revision=created["revision"])
    with pytest.raises(PlanningPoolError) as stale:
        pool.page("pool-restart", sid, page_size=2, cursor=cursor)
    assert stale.value.code == "stale_cursor"

    restarted = PlanningResourcePool()
    persisted = restarted.page("pool-restart", sid, page_size=2)
    assert persisted["selected_count"] == 1


def test_new_pool_idempotent_retry_accepts_returned_session_id():
    pool = PlanningResourcePool()
    request_id = "new-pool-retry-with-returned-session"
    candidate = _candidate(0, url="https://fixtures.example/new-pool-retry")

    first = pool.upsert(
        "pool-new-session-idempotency",
        [candidate],
        client_request_id=request_id,
        page_size=3,
    )
    replay = pool.upsert(
        "pool-new-session-idempotency",
        [candidate],
        planning_session_id=first["planning_session_id"],
        client_request_id=request_id,
        page_size=3,
    )

    assert replay == first
    assert pool.page(
        "pool-new-session-idempotency",
        first["planning_session_id"],
    )["revision"] == first["revision"]


def test_page_size_change_bumps_revision_and_invalidates_existing_cursor():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-page-size-revision", [_candidate(index) for index in range(4)], page_size=2)
    sid = created["planning_session_id"]
    cursor = pool.page("pool-page-size-revision", sid, page_size=2)["next_cursor"]

    changed = pool.upsert(
        "pool-page-size-revision",
        [],
        planning_session_id=sid,
        page_size=3,
        expected_revision=created["revision"],
    )
    assert changed["page_size"] == 3
    assert changed["revision"] == created["revision"] + 1
    with pytest.raises(PlanningPoolError) as stale:
        pool.page("pool-page-size-revision", sid, page_size=3, cursor=cursor)
    assert stale.value.code == "stale_cursor"


def test_page_projection_does_not_repair_anchor_pointer_or_revision():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-page-read-only", [_candidate(0)], page_size=2)
    sid = created["planning_session_id"]
    with orm.SessionLocal() as db:
        row = db.scalar(select(orm.PlanningResourcePoolORM).where(
            orm.PlanningResourcePoolORM.workspace_id == "pool-page-read-only",
            orm.PlanningResourcePoolORM.planning_session_id == sid,
        ))
        assert row is not None
        row.anchor_candidate_id = "stale-anchor"
        db.commit()

    projected = pool.page("pool-page-read-only", sid, page_size=2)
    assert projected["anchor_candidate_id"] == ""
    with orm.SessionLocal() as db:
        row = db.scalar(select(orm.PlanningResourcePoolORM).where(
            orm.PlanningResourcePoolORM.workspace_id == "pool-page-read-only",
            orm.PlanningResourcePoolORM.planning_session_id == sid,
        ))
        assert row is not None
        assert row.anchor_candidate_id == "stale-anchor"
        assert row.revision == created["revision"]


def test_pool_action_page_route_does_not_invoke_provider(client, monkeypatch):
    pool = PlanningResourcePool()
    created = pool.upsert("pool-route", [_candidate(0)], page_size=2)
    sid = created["planning_session_id"]

    def provider_must_not_run(*_args, **_kwargs):
        raise AssertionError("provider was called for pool page")

    monkeypatch.setattr("app.generic_knowledge_api.provider_for_session", provider_must_not_run)
    response = client.post(
        "/api/active-teaching/workspaces/pool-route/planning-assistant",
        json={"pool_action": "page", "planning_session_id": sid},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["resources"] == payload["candidate_page"]["items"]


def test_partial_pool_migration_repairs_tables_and_indexes(tmp_path: Path):
    database = tmp_path / "partial-pool.db"
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "0025_planning_assistant_cache")
    with sqlite3.connect(database) as connection:
        for table in (
            "orm_planning_resource_pools",
            "orm_planning_resource_candidates",
            "orm_planning_resource_pool_events",
            "orm_planning_resource_pool_requests",
        ):
            connection.execute(f"CREATE TABLE {table} (id VARCHAR(64) PRIMARY KEY)")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0028_video_collection_v1",)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(orm_planning_resource_candidates)")}
        assert {"stable_id", "selection_state", "retained", "direct_user_reference"} <= columns
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(orm_planning_resource_candidates)")}
        assert "ix_orm_planning_resource_candidate_pool_active" in indexes


def test_selection_retention_replacement_and_decision_contract():
    pool = PlanningResourcePool()
    initial = pool.upsert("pool-decisions", [_candidate(0), _candidate(1)], page_size=4)
    sid = initial["planning_session_id"]
    first_id, second_id = [item["candidate_id"] for item in initial["items"]]

    selected = pool.select("pool-decisions", planning_session_id=sid, candidate_ids=first_id, expected_revision=initial["revision"])
    assert selected["selected_count"] == 1
    assert next(item for item in selected["items"] if item["candidate_id"] == first_id)["retained"] is True

    replaced = pool.upsert("pool-decisions", [_candidate(2)], planning_session_id=sid, mode="replace_unselected", expected_revision=selected["revision"])
    visible_ids = {item["candidate_id"] for item in replaced["items"]}
    assert first_id in visible_ids
    assert second_id not in visible_ids

    with pytest.raises(PlanningPoolError) as rejected_selected:
        pool.reject("pool-decisions", planning_session_id=sid, candidate_ids=first_id, expected_revision=replaced["revision"])
    assert rejected_selected.value.code == "selected_candidate_pinned"

    deselected = pool.deselect("pool-decisions", planning_session_id=sid, candidate_ids=first_id, expected_revision=replaced["revision"])
    assert deselected["selected_count"] == 0
    assert next(item for item in deselected["items"] if item["candidate_id"] == first_id)["retained"] is False
    with pytest.raises(PlanningPoolError) as undo_unrejected:
        pool.undo("pool-decisions", planning_session_id=sid, candidate_ids=first_id, expected_revision=deselected["revision"])
    assert undo_unrejected.value.code == "candidate_not_rejected"

    rejected = pool.reject("pool-decisions", planning_session_id=sid, candidate_ids=first_id, expected_revision=deselected["revision"])
    restored = pool.undo("pool-decisions", planning_session_id=sid, candidate_ids=first_id, expected_revision=rejected["revision"])
    assert restored["selected_count"] == 0


def test_anchor_is_unique_and_clears_on_deselect():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-anchor", [_candidate(0), _candidate(1)], page_size=4)
    sid = created["planning_session_id"]
    ids = [item["candidate_id"] for item in created["items"]]

    first = pool.decide("pool-anchor", planning_session_id=sid, action="anchor", anchor_candidate_id=ids[0], expected_revision=created["revision"])
    support = pool.select("pool-anchor", planning_session_id=sid, candidate_ids=ids[1], expected_revision=first["revision"])
    assert support["selection"]["support"] == [ids[1]]
    second = pool.decide("pool-anchor", planning_session_id=sid, action="select_anchor", anchor_candidate_id=ids[1], expected_revision=support["revision"])
    assert second["selection"]["anchor"] == ids[1]
    assert ids[0] in second["selection"]["support"]

    cleared = pool.deselect("pool-anchor", planning_session_id=sid, candidate_ids=ids[1], expected_revision=second["revision"])
    assert cleared["selection"]["anchor"] is None
    assert cleared["anchor_candidate_id"] == ""
    assert ids[0] in cleared["selection"]["support"]


def test_anchor_survives_reselect_refresh_and_restart():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-anchor-idempotent", [_candidate(0), _candidate(1)], page_size=4)
    sid = created["planning_session_id"]
    anchor_id, support_id = [item["candidate_id"] for item in created["items"]]

    anchored = pool.decide(
        "pool-anchor-idempotent",
        planning_session_id=sid,
        action="set_anchor",
        anchor_candidate_id=anchor_id,
        expected_revision=created["revision"],
    )
    reselected = pool.select(
        "pool-anchor-idempotent",
        planning_session_id=sid,
        candidate_ids=anchor_id,
        expected_revision=anchored["revision"],
    )
    assert reselected["selection"]["anchor"] == anchor_id
    assert reselected["anchor_candidate_id"] == anchor_id
    assert next(item for item in reselected["items"] if item["candidate_id"] == anchor_id)["selection_state"] == "anchor"

    selected_support = pool.select(
        "pool-anchor-idempotent",
        planning_session_id=sid,
        candidate_ids=support_id,
        expected_revision=reselected["revision"],
    )
    assert selected_support["selection"]["anchor"] == anchor_id
    assert selected_support["selection"]["support"] == [support_id]

    refreshed = pool.upsert(
        "pool-anchor-idempotent",
        [_candidate(0), _candidate(1), _candidate(2)],
        planning_session_id=sid,
        mode="replace_unselected",
        expected_revision=selected_support["revision"],
    )
    assert refreshed["selection"]["anchor"] == anchor_id
    assert refreshed["anchor_candidate_id"] == anchor_id

    restarted = PlanningResourcePool()
    persisted = restarted.page("pool-anchor-idempotent", sid, page_size=4)
    assert persisted["selection"]["anchor"] == anchor_id
    assert persisted["anchor_candidate_id"] == anchor_id


def test_anchor_reject_requires_deselect_and_clears_pointer_before_reject():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-anchor-reject", [_candidate(0), _candidate(1)], page_size=4)
    sid = created["planning_session_id"]
    anchor_id = created["items"][0]["candidate_id"]
    anchored = pool.decide(
        "pool-anchor-reject",
        planning_session_id=sid,
        action="set_anchor",
        anchor_candidate_id=anchor_id,
        expected_revision=created["revision"],
    )

    with pytest.raises(PlanningPoolError) as pinned:
        pool.reject(
            "pool-anchor-reject",
            planning_session_id=sid,
            candidate_ids=anchor_id,
            expected_revision=anchored["revision"],
        )
    assert pinned.value.code == "selected_candidate_pinned"
    still_anchored = pool.page("pool-anchor-reject", sid, page_size=4)
    assert still_anchored["selection"]["anchor"] == anchor_id
    assert still_anchored["anchor_candidate_id"] == anchor_id

    deselected = pool.deselect(
        "pool-anchor-reject",
        planning_session_id=sid,
        candidate_ids=anchor_id,
        expected_revision=still_anchored["revision"],
    )
    assert deselected["selection"]["anchor"] is None
    assert deselected["anchor_candidate_id"] == ""

    rejected = pool.reject(
        "pool-anchor-reject",
        planning_session_id=sid,
        candidate_ids=anchor_id,
        expected_revision=deselected["revision"],
    )
    assert rejected["selection"]["anchor"] is None
    assert rejected["anchor_candidate_id"] == ""
    assert anchor_id in rejected["selection"]["rejected"]


def test_url_identity_overrides_provider_id_and_bad_port_is_safe():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-identity", [
        {"url": "https://example.test/a", "stable_id": "same-provider-id", "title": "A"},
        {"url": "https://example.test/b", "stable_id": "same-provider-id", "title": "B"},
    ])
    assert len(created["items"]) == 2
    assert len({item["stable_id"] for item in created["items"]}) == 2

    with pytest.raises(PlanningPoolError) as malformed:
        pool.upsert("pool-identity", [{"url": "https://example.test:not-a-port/x", "title": "bad"}], planning_session_id=created["planning_session_id"], expected_revision=created["revision"])
    assert malformed.value.code == "invalid_url"


def test_video_direct_reference_is_audited_but_never_active_or_selectable():
    pool = PlanningResourcePool()
    created = pool.upsert(
        "pool-video-disabled",
        [{
            "url": "https://www.youtube.com/watch?v=video-only",
            "title": "User video",
            "media_kind": "video",
            "direct_user_reference": True,
        }],
    )
    assert created["all_active_count"] == 0
    assert created["items"] == []
    row = pool.candidates("pool-video-disabled", created["planning_session_id"], include_inactive=True)[0]
    assert row["selection_state"] == "rejected"
    with pytest.raises(PlanningPoolError) as blocked:
        pool.select(
            "pool-video-disabled",
            planning_session_id=created["planning_session_id"],
            candidate_ids=row["candidate_id"],
            expected_revision=created["revision"],
        )
    assert blocked.value.code == "video_material_disabled"


def test_legacy_video_anchor_is_revoked_and_pointer_cleared():
    pool = PlanningResourcePool()
    created = pool.upsert(
        "pool-video-legacy-anchor",
        [{
            "url": "https://www.youtube.com/watch?v=legacy",
            "title": "Legacy video",
            "media_kind": "video",
            "direct_user_reference": True,
        }],
    )
    # Simulate a pre-policy row that had already been selected/anchored.
    from sqlalchemy import select
    with orm.SessionLocal() as db:
        pool_row = db.scalar(select(orm.PlanningResourcePoolORM).where(orm.PlanningResourcePoolORM.planning_session_id == created["planning_session_id"]))
        candidate = db.scalar(select(orm.PlanningResourceCandidateORM).where(orm.PlanningResourceCandidateORM.pool_id == pool_row.id))
        candidate.active = True
        candidate.selection_state = "anchor"
        pool_row.anchor_candidate_id = candidate.candidate_id
        db.commit()
    revoked = pool.revoke_unsupported(
        "pool-video-legacy-anchor",
        planning_session_id=created["planning_session_id"],
        candidate_ids=[candidate.candidate_id],
    )
    assert revoked["anchor_candidate_id"] == ""
    assert revoked["selection"]["anchor"] is None
    assert revoked["items"] == []


def test_idempotency_and_revision_race_are_serialized():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-race", [_candidate(0)], page_size=4)
    sid = created["planning_session_id"]
    cid = created["items"][0]["candidate_id"]

    def run(request_id: str, expected_revision: int):
        try:
            return ("ok", pool.select("pool-race", planning_session_id=sid, candidate_ids=cid, expected_revision=expected_revision, client_request_id=request_id))
        except PlanningPoolError as exc:
            return (exc.code, None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        same_request = list(executor.map(lambda _: run("same-request", created["revision"]), range(2)))
    assert [status for status, _ in same_request] == ["ok", "ok"]
    assert same_request[0][1]["revision"] == same_request[1][1]["revision"]

    expected_revision = same_request[0][1]["revision"]

    def append(request_id: str):
        try:
            response = pool.upsert(
                "pool-race",
                [{"url": f"https://materials.example/{request_id}", "title": request_id}],
                planning_session_id=sid,
                expected_revision=expected_revision,
                client_request_id=request_id,
            )
            return "ok", response
        except PlanningPoolError as exc:
            return exc.code, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        competing = list(executor.map(append, ["different-a", "different-b"]))
    assert sorted(status for status, _ in competing) == ["ok", "stale_revision"]


def test_idempotency_key_is_bound_to_decision_payload():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-idempotency-binding", [_candidate(0), _candidate(1)])
    sid = created["planning_session_id"]
    first_id, second_id = [item["candidate_id"] for item in created["items"]]

    selected = pool.select(
        "pool-idempotency-binding",
        planning_session_id=sid,
        candidate_ids=first_id,
        expected_revision=created["revision"],
        client_request_id="bound-decision",
    )
    replay = pool.select(
        "pool-idempotency-binding",
        planning_session_id=sid,
        candidate_ids=first_id,
        expected_revision=created["revision"],
        client_request_id="bound-decision",
    )
    assert replay == selected

    with pytest.raises(PlanningPoolError) as conflict:
        pool.deselect(
            "pool-idempotency-binding",
            planning_session_id=sid,
            candidate_ids=first_id,
            client_request_id="bound-decision",
        )
    assert conflict.value.code == "idempotency_conflict"
    unchanged = pool.page("pool-idempotency-binding", sid)
    assert unchanged["revision"] == selected["revision"]
    assert first_id in unchanged["selection"]["selected"]
    assert second_id in {item["candidate_id"] for item in unchanged["items"]}


def test_supersede_idempotency_replays_identical_request_only():
    pool = PlanningResourcePool()
    created = pool.upsert("pool-idempotency-supersede", [_candidate(0), _candidate(1)])
    sid = created["planning_session_id"]
    first_id, second_id = [item["candidate_id"] for item in created["items"]]

    superseded = pool.supersede_blocked(
        "pool-idempotency-supersede",
        planning_session_id=sid,
        candidate_ids=first_id,
        expected_revision=created["revision"],
        client_request_id="bound-supersede",
    )
    replay = pool.supersede_blocked(
        "pool-idempotency-supersede",
        planning_session_id=sid,
        candidate_ids=first_id,
        expected_revision=created["revision"],
        client_request_id="bound-supersede",
    )
    assert replay == superseded

    with pytest.raises(PlanningPoolError) as conflict:
        pool.supersede_blocked(
            "pool-idempotency-supersede",
            planning_session_id=sid,
            candidate_ids=second_id,
            client_request_id="bound-supersede",
        )
    assert conflict.value.code == "idempotency_conflict"
