from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import orm
from app.video_collection import (
    BudgetExceededError,
    StaleCursorError,
    VideoCollectionError,
    VideoCollectionService,
)


VIDEO_TABLES = [
    orm.VideoCollectionORM.__table__,
    orm.VideoEpisodeORM.__table__,
    orm.VideoSubtitleSegmentORM.__table__,
    orm.VideoCollectionJobORM.__table__,
    orm.VideoSummaryORM.__table__,
]


@pytest.fixture()
def service(tmp_path: Path) -> VideoCollectionService:
    engine = create_engine(f"sqlite:///{(tmp_path / 'video.db').as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    orm.KnowledgeMapBase.metadata.create_all(engine, tables=VIDEO_TABLES)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False, future=True)
    result = VideoCollectionService(factory)
    yield result
    engine.dispose()


def _collection(service: VideoCollectionService, workspace_id: str = "workspace-a", suffix: str = "main") -> dict:
    return service.create_collection(
        workspace_id=workspace_id,
        provider="fixture",
        provider_collection_id=f"collection-{suffix}",
        canonical_url=f"https://video.example/playlist?list={suffix}",
        title=f"Collection {suffix}",
        idempotency_key=f"create-{workspace_id}-{suffix}",
        metadata={"language": "zh-CN"},
    )


def _episode(index: int, *, selected: bool = True, duration: float = 60) -> dict:
    return {
        "provider_episode_id": f"episode-{index}",
        "stable_id": f"caller-controlled-{index}",
        "url": f"https://video.example/watch?v={index}",
        "title": f"Episode {index}",
        "ordinal": index,
        "duration_seconds": duration,
        "selected": selected,
        "metadata": {"language": "zh-CN"},
    }


def test_workspace_scoping_and_secret_rejection(service: VideoCollectionService):
    first = _collection(service, "workspace-a", "same")
    second = _collection(service, "workspace-b", "same")
    assert first["id"] != second["id"]
    assert service.get_collection(first["id"], workspace_id="workspace-a")
    assert service.get_collection(first["id"], workspace_id="workspace-b") is None

    with pytest.raises(VideoCollectionError) as query_secret:
        service.create_collection(
            workspace_id="workspace-a",
            canonical_url="https://video.example/list?api_key=do-not-store",
        )
    assert query_secret.value.code == "SENSITIVE_DATA_REJECTED"

    with pytest.raises(VideoCollectionError) as metadata_secret:
        service.create_collection(
            workspace_id="workspace-a",
            canonical_url="https://video.example/other",
            metadata={"provider_session_id": "ps_private"},
        )
    assert metadata_secret.value.code == "SENSITIVE_DATA_REJECTED"


def test_manifest_accepts_1000_rejects_1001_atomically_and_ignores_spoofed_ids(service: VideoCollectionService):
    collection = _collection(service)
    accepted = service.upsert_manifest(collection["id"], [_episode(index) for index in range(1000)], reported_count=1000)
    assert accepted["enumerated_count"] == 1000
    assert accepted["selected_count"] == 1000
    assert len({item["stable_id"] for item in accepted["episodes"]}) == 1000
    assert all(not item["stable_id"].startswith("caller-controlled") for item in accepted["episodes"])

    before = service.get_collection(collection["id"])
    with pytest.raises(VideoCollectionError) as oversized:
        service.upsert_manifest(collection["id"], [_episode(index) for index in range(1001)], reported_count=1001)
    assert oversized.value.code == "MANIFEST_LIMIT_EXCEEDED"
    after = service.get_collection(collection["id"])
    assert after["manifest_revision"] == before["manifest_revision"]
    assert after["enumerated_count"] == 1000

    with pytest.raises(VideoCollectionError) as spoofed_duplicate:
        service.upsert_manifest(
            collection["id"],
            [
                {**_episode(1), "stable_id": "left"},
                {**_episode(1), "stable_id": "right", "ordinal": 2},
            ],
        )
    assert spoofed_duplicate.value.code == "DUPLICATE_MANIFEST_IDENTITY"


def test_manifest_cursor_is_bidirectional_and_revision_bound(service: VideoCollectionService):
    collection = _collection(service)
    service.upsert_manifest(collection["id"], [_episode(index) for index in range(205)])
    first = service.manifest_page(collection["id"], page_size=100)
    assert first["page"] == 1
    assert len(first["items"]) == 100
    assert first["previous_cursor"] is None
    second = service.manifest_page(collection["id"], page_size=100, cursor=first["next_cursor"])
    assert second["page"] == 2
    assert second["previous_cursor"]
    first_again = service.manifest_page(collection["id"], page_size=100, cursor=second["previous_cursor"])
    assert [item["id"] for item in first_again["items"]] == [item["id"] for item in first["items"]]

    service.upsert_manifest(collection["id"], [_episode(index) for index in range(206)])
    with pytest.raises(StaleCursorError):
        service.manifest_page(collection["id"], page_size=100, cursor=first["next_cursor"])


def test_job_pause_resume_cancel_and_bounded_parallel_claims(service: VideoCollectionService):
    collection = _collection(service)
    manifest = service.upsert_manifest(collection["id"], [_episode(index) for index in range(4)])
    job = service.create_job(collection["id"], idempotency_key="transcribe-1", max_parallel=2)
    assert service.create_job(collection["id"], idempotency_key="transcribe-1", max_parallel=2)["id"] == job["id"]

    first = service.claim_next_episode(job["id"], worker_id="worker-1")
    second = service.claim_next_episode(job["id"], worker_id="worker-2")
    assert first and second and first["id"] != second["id"]
    assert service.claim_next_episode(job["id"], worker_id="worker-3") is None

    service.checkpoint_episode(job["id"], first["id"], {"subtitle": "ready"}, status="ready")
    paused = service.pause_job(job["id"])
    assert paused["status"] == "paused"
    assert service.coverage(collection["id"])["ready"] == 1
    assert service.resume_job(job["id"])["status"] == "queued"

    resumed = service.claim_next_episode(job["id"], worker_id="worker-4")
    assert resumed and resumed["id"] != first["id"]
    cancelled = service.cancel_job(job["id"])
    assert cancelled["status"] == "cancelled"
    coverage = service.coverage(collection["id"])
    assert coverage["ready"] == 1
    assert coverage["complete"] is False
    assert manifest["selected_count"] == 4


def test_one_active_job_per_type_prevents_duplicate_network_or_provider_work(service: VideoCollectionService):
    collection = _collection(service, suffix="single-flight")
    service.upsert_manifest(collection["id"], [_episode(index) for index in range(2)])
    first = service.create_job(
        collection["id"],
        job_type="transcribe",
        idempotency_key="first-request",
        request={"manifest_revision": 1, "requested_languages": ["zh.*"]},
    )
    replay = service.create_job(
        collection["id"],
        job_type="transcribe",
        idempotency_key="second-request-same-payload",
        request={"manifest_revision": 1, "requested_languages": ["zh.*"]},
    )
    assert replay["id"] == first["id"]

    with pytest.raises(VideoCollectionError) as conflict:
        service.create_job(
            collection["id"],
            job_type="transcribe",
            idempotency_key="different-language",
            request={"manifest_revision": 1, "requested_languages": ["en.*"]},
        )
    assert conflict.value.code == "VIDEO_COLLECTION_JOB_IN_PROGRESS"


def test_segments_hierarchy_and_source_change_invalidate_old_evidence(service: VideoCollectionService):
    collection = _collection(service)
    manifest = service.upsert_manifest(collection["id"], [_episode(1)])
    episode = manifest["episodes"][0]
    stored = service.insert_segments(
        episode["id"],
        [
            {"ordinal": 0, "start_ms": 0, "end_ms": 1500, "text": "矩阵表示线性变换"},
            {"ordinal": 1, "start_ms": 1500, "end_ms": 3000, "text": "基向量决定坐标表示"},
        ],
    )
    assert stored["segment_count"] == 2
    segment = stored["segments"][0]
    episode_summary = service.store_summary(
        collection["id"],
        scope_type="episode",
        scope_id=episode["id"],
        episode_id=episode["id"],
        provider_fingerprint="mock-provider-v1",
        summary_text="本集介绍矩阵与变换。",
        evidence=[{"segment_id": segment["id"], "start_ms": 0, "end_ms": 1500, "quote_hash": segment["quote_hash"]}],
    )
    batch = service.store_summary(
        collection["id"],
        scope_type="batch",
        scope_id="batch-1",
        provider_fingerprint="mock-provider-v1",
        summary_text="本批次建立线性代数基础。",
        evidence=[{"summary_id": episode_summary["id"]}],
    )
    overall = service.store_summary(
        collection["id"],
        scope_type="collection",
        scope_id=collection["id"],
        provider_fingerprint="mock-provider-v1",
        summary_text="课程从表示方法逐步进入抽象结构。",
        evidence=[{"summary_id": batch["id"]}],
    )
    assert overall["scope_type"] == "collection"

    changed = service.upsert_manifest(collection["id"], [{**_episode(1), "duration_seconds": 61}])
    changed_episode = changed["episodes"][0]
    assert changed_episode["status"] == "unprocessed"
    with service.session_factory() as db:
        assert db.query(orm.VideoSubtitleSegmentORM).filter_by(episode_id=episode["id"]).count() == 0
        stale = db.query(orm.VideoSummaryORM).filter_by(collection_id=collection["id"]).all()
        assert stale and all(item.status == "stale" for item in stale)


def test_zero_selection_never_claims_complete_and_budget_fails_closed(service: VideoCollectionService):
    collection = _collection(service)
    service.upsert_manifest(collection["id"], [_episode(0, selected=False)])
    coverage = service.coverage(collection["id"])
    assert coverage["coverage_ratio"] == 0
    assert coverage["complete"] is False
    assert coverage["warning"] == "no_selected_episodes"
    job = service.create_job(collection["id"], idempotency_key="empty-job")
    assert service.finalize_job(job["id"])["status"] == "completed_with_failures"

    selected_collection = _collection(service, suffix="budget")
    manifest = service.upsert_manifest(selected_collection["id"], [_episode(0)])
    huge_text = "字" * (8 * 1024 * 1024)
    with pytest.raises(BudgetExceededError):
        service.insert_segments(
            manifest["episodes"][0]["id"],
            [{"ordinal": 0, "start_ms": 0, "end_ms": 1000, "text": huge_text}],
        )


def test_0028_upgrade_twice_creates_expected_constraints(tmp_path: Path):
    database = tmp_path / "migration.db"
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0028_video_collection_v1",)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "orm_video_collections",
            "orm_video_episodes",
            "orm_video_subtitle_segments",
            "orm_video_collection_jobs",
            "orm_video_summaries",
        } <= tables
        collection_columns = {row[1] for row in connection.execute("PRAGMA table_info(orm_video_collections)")}
        assert "workspace_id" in collection_columns
