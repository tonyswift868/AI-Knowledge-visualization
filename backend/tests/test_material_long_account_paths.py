"""Real filesystem regression for uploads below hashed desktop account roots."""
import os
from pathlib import Path

import pytest

from app import active_teaching
from app.plain_file_copy import _native_path


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
@pytest.mark.parametrize("long_account_root", [True, False])
def test_upload_parse_deduplicate_and_delete_in_long_account_root(client, tmp_path, monkeypatch, long_account_root):
    upload_root = (tmp_path / ("a" * 64) / ("b" * 64) / "data/active_teaching/uploads"
                   if long_account_root else tmp_path / "uploads")
    monkeypatch.setattr(active_teaching, "UPLOAD_ROOT", upload_root)
    workspace = "personal_ws_" + "c" * 64 + "-active"
    if long_account_root:
        assert len(str(upload_root / workspace)) > 260
    payload = b"Python variables store values. Lists hold items. Loops repeat instructions."
    url = f"/api/active-teaching/workspaces/{workspace}/materials"
    response = client.post(url, files={"file": ("lesson.txt", payload, "text/plain")})
    assert response.status_code == 200, response.text
    material = response.json()
    assert material["status"] == "ready"
    assert "stored_path" not in material
    assert any("Python variables" in part["text"] for part in material["chunks"])
    with active_teaching._connect() as connection:
        stored = Path(connection.execute(
            "SELECT stored_path FROM active_materials WHERE id=?", (material["id"],)
        ).fetchone()[0])
    assert stored.read_bytes() == payload
    assert _native_path(upload_root).resolve() in stored.resolve().parents
    duplicate = client.post(url, files={"file": ("lesson.txt", payload, "text/plain")})
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == material["id"]
    assert duplicate.json()["deduplicated"] is True
    if not long_account_root:
        # Existing records predate extended path storage. Physical deletion
        # must still recognize their ordinary Windows path as account-owned.
        with active_teaching._connect() as connection:
            connection.execute("UPDATE active_materials SET stored_path=? WHERE id=?",
                               (str(stored).removeprefix("\\\\?\\"), material["id"]))
            connection.commit()
    deleted = client.delete(url + "/" + material["id"])
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["reversible"] is True
    assert stored.exists()
    purged = client.request("DELETE", url + "/" + material["id"],
                            json={"physical": True, "confirm_unreferenced": True})
    assert purged.status_code == 200, purged.text
    assert not stored.exists()


def test_physical_purge_does_not_delete_outside_upload_root(client, tmp_path):
    url = "/api/active-teaching/workspaces/qa-owned-boundary/materials"
    response = client.post(url, files={"file": ("lesson.txt", b"Synthetic lesson.", "text/plain")})
    assert response.status_code == 200, response.text
    material_id = response.json()["id"]
    outside = tmp_path / "outside-upload-root.txt"
    outside.write_bytes(b"must remain")
    with active_teaching._connect() as connection:
        connection.execute("UPDATE active_materials SET stored_path=? WHERE id=?",
                           (str(outside), material_id))
        connection.commit()
    response = client.request("DELETE", url + "/" + material_id,
                              json={"physical": True, "confirm_unreferenced": True})
    assert response.status_code == 200, response.text
    assert outside.read_bytes() == b"must remain"
