from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from fastapi.testclient import TestClient
from app import db, orm
from app.main import app

@pytest.fixture(autouse=True)
def isolated_databases(tmp_path_factory, monkeypatch):
    """Ensure every test writes only to its own legacy and ORM databases."""
    previous_orm_db = orm.ORM_DB
    database_dir = tmp_path_factory.mktemp("mentorflow-databases")
    monkeypatch.setattr(db, "DB_PATH", database_dir / "legacy.db")
    monkeypatch.setattr(db, "DATA_DIR", database_dir)
    db.init_db()
    orm.configure_orm(database_dir / "mentorflow_v2.db")
    orm.init_orm()
    try:
        yield
    finally:
        orm.engine.dispose()
        orm.configure_orm(previous_orm_db)

@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c
