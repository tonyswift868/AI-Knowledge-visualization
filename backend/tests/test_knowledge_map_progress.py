from __future__ import annotations

from desktop.knowledge_map_view import layered_positions
from app import generic_knowledge_api as generic_api


def test_progress_contract_is_checkpoint_based_and_versioned() -> None:
    total = generic_api._v1_progress_total_units(material_count=4, chunk_count=9)
    progress = generic_api._v1_progress_defaults(status="queued", total_units=total)

    assert progress["schema"] == "mentorflow.knowledge-map-progress.v1"
    assert progress["version"] == 1
    assert progress["phase"] == "queued"
    assert progress["completed_units"] == 0
    expected_batches = (9 + generic_api.PROVIDER_BATCH_SIZE - 1) // generic_api.PROVIDER_BATCH_SIZE
    assert progress["total_units"] == 1 + 4 + expected_batches + 1 + 1
    assert progress["percent"] == 0.0
    assert "message" in progress
    assert "sequence" in progress
    assert "updated_at" in progress
    assert progress["last_good_revision"] is None
    assert progress["last_good_revision_id"] == ""


def test_large_disconnected_map_is_packed_across_balanced_rows() -> None:
    nodes = [
        {"id": f"node-{index}", "name": f"节点 {index}", "explanation": "可追溯说明"}
        for index in range(150)
    ]
    positions = layered_positions(nodes, [])
    xs = [position[0] for position in positions.values()]
    ys = [position[1] for position in positions.values()]

    assert len(positions) == 150
    assert len(set(xs)) >= 10
    assert max(xs) < 5000
    assert max(ys) < 3000
