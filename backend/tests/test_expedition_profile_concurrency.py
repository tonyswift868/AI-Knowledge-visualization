"""Regression coverage for concurrent first-request Expedition seeding."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import expedition_service, orm
from app.expedition_orm import ExpeditionProfileORM


@pytest.fixture()
def fresh_expedition_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Use a fresh SQLite file so each concurrency level starts unseeded."""

    engine = create_engine(
        f"sqlite:///{tmp_path / 'expedition.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(orm, "engine", engine)
    monkeypatch.setattr(orm, "SessionLocal", sessions)
    monkeypatch.setattr(expedition_service, "SessionLocal", sessions)
    expedition_service.init_orm()
    try:
        yield sessions
    finally:
        engine.dispose()


@pytest.mark.parametrize("workers", [20, 50, 100])
def test_concurrent_first_seed_creates_one_local_profile(fresh_expedition_db, workers: int):
    sessions = fresh_expedition_db

    def seed_once() -> str:
        with sessions() as session:
            profile = expedition_service._seed(session)
            session.commit()
            return profile.id

    with ThreadPoolExecutor(max_workers=workers) as pool:
        profile_ids = list(pool.map(lambda _: seed_once(), range(workers)))

    assert set(profile_ids) == {expedition_service.PROFILE_ID}
    with sessions() as session:
        profiles = session.query(ExpeditionProfileORM).filter_by(user_key="local").all()
        assert len(profiles) == 1
        assert profiles[0].id == expedition_service.PROFILE_ID
