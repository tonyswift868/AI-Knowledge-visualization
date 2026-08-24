"""Acceptance-side tests for the Learning Expedition sidecar.

These tests deliberately isolate both the legacy sqlite database and the ORM
database.  They exercise the public service/API boundary and the pure battle
engine without changing expedition implementation code.
"""

from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, expedition_service, orm
from app.expedition_data import HEROES
from app.expedition_engine import EVENT_TYPES, BattleEngine, stable_seed
from app.expedition_orm import (
    HeroAwakeningORM,
    HeroOwnershipORM,
    HeroStarProgressORM,
)
from app.main import app


@pytest.fixture()
def expedition_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Run the app against temporary legacy and ORM databases."""

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    isolated_engine = create_engine(
        f"sqlite:///{tmp_path / 'mentorflow_v2.db'}",
        connect_args={"check_same_thread": False},
    )
    isolated_sessions = sessionmaker(isolated_engine, expire_on_commit=False)
    monkeypatch.setattr(orm, "engine", isolated_engine)
    monkeypatch.setattr(orm, "SessionLocal", isolated_sessions)
    monkeypatch.setattr(expedition_service, "SessionLocal", isolated_sessions)
    with TestClient(app) as client:
        yield client
    isolated_engine.dispose()


def _add_owned_heroes() -> list[str]:
    """Complete a five-person formation in the test profile."""

    hero_ids = [item["hero_id"] for item in HEROES[:5]]
    with expedition_service.SessionLocal() as session:
        profile = session.query(expedition_service.ExpeditionProfileORM).filter_by(user_key="local").one()
        for hero_id in hero_ids:
            if not session.query(HeroOwnershipORM).filter_by(profile_id=profile.id, hero_id=hero_id).first():
                ownership = HeroOwnershipORM(
                    id=f"test_own_{hero_id}",
                    profile_id=profile.id,
                    hero_id=hero_id,
                    stars=1,
                    fragments=0,
                    awakened=False,
                )
                session.add(ownership)
                session.flush()
                session.add(
                    HeroStarProgressORM(
                        id=f"test_star_{hero_id}",
                        ownership_id=ownership.id,
                        current_stars=1,
                        fragments=0,
                    )
                )
                session.add(
                    HeroAwakeningORM(
                        id=f"test_awaken_{hero_id}",
                        ownership_id=ownership.id,
                        unlocked=False,
                    )
                )
        session.commit()
    return hero_ids


def test_verified_reward_is_idempotent_and_never_negative(expedition_client):
    first = expedition_service.publish_verified_learning_reward(
        "workspace-a", "learning-run-1", 60
    )
    duplicate = expedition_service.publish_verified_learning_reward(
        "workspace-a", "learning-run-1", 60
    )
    zero = expedition_service.publish_verified_learning_reward(
        "workspace-a", "learning-run-negative", -20
    )

    assert first["granted"] == 60
    assert duplicate["idempotent"] is True
    assert duplicate["granted"] == 60
    assert zero["granted"] == 0
    profile = expedition_service.get_profile()
    assert profile["crystals"] == 60
    assert profile["training_exp"] == 60
    assert profile["crystals"] >= 0
    assert profile["training_exp"] >= 0
    ledger = expedition_service.get_ledger()["items"]
    assert len(ledger) == 1
    assert ledger[0]["workspace_id"] == "workspace-a"
    assert ledger[0]["learning_run_id"] == "learning-run-1"


def test_concurrent_reward_publication_has_one_ledger_row(expedition_client):
    def publish():
        return expedition_service.publish_verified_learning_reward("workspace-concurrent", "run-concurrent", 15)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: publish(), range(4)))
    assert sum(not item.get("idempotent", False) for item in results) == 1
    assert len(expedition_service.get_ledger()["items"]) == 1


def test_first_profile_and_lineups_seed_canonical_grid_idempotently(expedition_client):
    first_profile = expedition_service.get_profile()
    first_lineups = expedition_service.lineups()

    assert first_lineups["formation"] == {
        "type": "grid_3x3",
        "cells": [f"cell_{index}" for index in range(9)],
        "rules": "从九宫格九个位置中自由选择五个角色；行列决定站位与行动顺序",
    }
    assert len(first_lineups["items"]) == 1
    active = first_lineups["items"][0]
    assert active["active"] is True
    assert active["formation_type"] == "grid_3x3"
    assert active["lineup_id"] == first_profile["active_lineup_id"]
    assert len(active["members"]) == 5
    member_cells = [member["slot_id"] for member in active["members"]]
    assert len(set(member_cells)) == 5
    assert all(cell in first_lineups["formation"]["cells"] for cell in member_cells)
    assert all(member["hero_id"] for member in active["members"])

    second_lineups = expedition_service.lineups()
    assert second_lineups == first_lineups


def test_lineup_rejects_duplicate_heroes_and_persists_five_positions(expedition_client):
    expedition_service.get_profile()
    hero_ids = _add_owned_heroes()
    duplicate = expedition_client.post(
        "/api/expedition/lineups",
        json={
            "name": "invalid",
            "members": [
                {"slot_id": "slot_1", "hero_id": hero_ids[0]},
                {"slot_id": "slot_2", "hero_id": hero_ids[0]},
                {"slot_id": "slot_3", "hero_id": hero_ids[1]},
                {"slot_id": "slot_4", "hero_id": hero_ids[2]},
                {"slot_id": "slot_5", "hero_id": hero_ids[3]},
            ],
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "DUPLICATE_HERO"

    valid = expedition_client.post(
        "/api/expedition/lineups",
        json={
            "name": "five-position-test",
            "stance": "vanguard",
            "members": [
                {"slot_id": f"slot_{index + 1}", "hero_id": hero_id}
                for index, hero_id in enumerate(hero_ids)
            ],
        },
    )
    assert valid.status_code == 200, valid.text
    item = valid.json()["item"]
    assert len(item["members"]) == 5
    assert len({member["hero_id"] for member in item["members"]}) == 5
    activated = expedition_client.post(f"/api/expedition/lineups/{item['lineup_id']}/activate")
    assert activated.status_code == 200, activated.text


def test_fixed_seed_produces_same_event_stream_and_lineup_changes_seed():
    heroes = HEROES[:5]
    first = BattleEngine(
        seed=stable_seed("acceptance", 1),
        heroes=heroes,
        enemy_ids=["shield_guard", "row_crossbow", "field_medic", "counter_guard", "bomb_golem"],
        stance="balanced",
    ).run()
    second = BattleEngine(
        seed=stable_seed("acceptance", 1),
        heroes=heroes,
        enemy_ids=["shield_guard", "row_crossbow", "field_medic", "counter_guard", "bomb_golem"],
        stance="balanced",
    ).run()
    changed = BattleEngine(
        seed=stable_seed("acceptance", 1, "different-lineup"),
        heroes=list(reversed(heroes)),
        enemy_ids=["shield_guard", "row_crossbow", "field_medic", "counter_guard", "bomb_golem"],
        stance="balanced",
    ).run()

    assert first["events"] == second["events"]
    assert first["winner"] == second["winner"]
    assert first["events"] != changed["events"]
    assert {event["event_type"] for event in first["events"]} <= EVENT_TYPES
    assert all("SPEED" not in unit for unit in first["units"])


def test_gacha_is_server_decided_and_idempotent_without_negative_balance(expedition_client):
    expedition_service.publish_verified_learning_reward("workspace-a", "gacha-funds", 400)
    before = expedition_service.get_profile()["crystals"]
    payload = {
        "banner_id": "character_standard",
        "count": 1,
        "idempotency_key": "pull-once",
        "results": [{"hero_id": "client-forged", "rarity": "legendary"}],
        "seed": 1,
        "crystals": 999999,
    }
    first = expedition_client.post("/api/expedition/gacha/pull", json=payload)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["cost"] == 30
    assert first_body["crystals"] == before - 30
    assert all(result.get("hero_id") != "client-forged" for result in first_body["results"])

    duplicate = expedition_client.post("/api/expedition/gacha/pull", json=payload)
    assert duplicate.status_code == 200, duplicate.text
    duplicate_body = duplicate.json()
    assert duplicate_body["idempotent"] is True
    assert duplicate_body["results"] == first_body["results"]
    assert expedition_service.get_profile()["crystals"] == before - 30


def test_stage_is_infinite_and_battle_events_are_persisted_server_side(expedition_client):
    expedition_service.get_profile()
    for number in (1, 10, 50, 100, 1000, 10000):
        response = expedition_client.get(f"/api/expedition/stages/{number}")
        assert response.status_code == 200, response.text
        assert response.json()["stage_number"] == number
    assert expedition_client.get("/api/expedition/stages/10").json()["boss"]
    assert expedition_client.get("/api/expedition/stages/10000").json()["stage_number"] == 10000
    assert expedition_client.get("/api/expedition/stages/0").status_code == 400

    hero_ids = _add_owned_heroes()
    lineup = expedition_client.post(
        "/api/expedition/lineups",
        json={
            "name": "battle-test",
            "members": [
                {"slot_id": f"slot_{index + 1}", "hero_id": hero_id}
                for index, hero_id in enumerate(hero_ids)
            ],
        },
    ).json()["item"]
    expedition_client.post(f"/api/expedition/lineups/{lineup['lineup_id']}/activate")
    battle = expedition_client.post(
        "/api/expedition/stages/1/challenge",
        json={"winner": "ally", "events": [{"event_type": "FORGED"}]},
    )
    assert battle.status_code == 200, battle.text
    body = battle.json()
    assert body["event_count"] > 0
    assert body["result"]["winner"] in {"ally", "enemy", "draw"}
    assert "FORGED" not in {event["event_type"] for event in expedition_client.get(f"/api/expedition/battles/{body['battle_id']}/events").json()["events"]}


def test_asset_registry_allows_only_registered_ids_and_rejects_path_traversal(expedition_client):
    expedition_service.get_profile()
    valid = expedition_client.get("/api/expedition/assets/hero:jinxiao:portrait")
    assert valid.status_code == 200, valid.text
    asset = valid.json()
    assert asset["source"] == "generated"
    assert asset["sha256"]
    assert ".." not in asset["path"]

    with pytest.raises(expedition_service.ExpeditionError) as error:
        expedition_service.assets("../secret")
    assert error.value.code == "INVALID_ASSET_ID"
    missing = expedition_client.get("/api/expedition/assets/hero:missing:portrait")
    assert missing.status_code == 404
