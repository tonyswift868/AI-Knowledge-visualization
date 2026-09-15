"""Convergence checks for account-owned material I/O on Windows."""
import hashlib
from pathlib import Path

import pytest

from app import active_teaching
from app.plain_file_copy import _native_path, copy_plain_file_exact


@pytest.mark.parametrize("filename", ["课程资料.txt", "resource-" + "r" * 170 + ".txt"])
def test_long_upload_cross_account_purge_and_missing_file(client, tmp_path, monkeypatch, filename):
    roots = [tmp_path / (name * 64) / ("w" * 64) / "nested/data/uploads" for name in ("a", "b")]
    workspace = "personal_ws_" + "c" * 64 + "-active"
    url = f"/api/active-teaching/workspaces/{workspace}/materials"
    monkeypatch.setattr(active_teaching, "UPLOAD_ROOT", roots[0])
    response = client.post(url, files={"file": (filename, b"Synthetic path lesson.", "text/plain")})
    assert response.status_code == 200, response.text
    material_id = response.json()["id"]
    with active_teaching._connect() as connection:
        stored = Path(connection.execute("SELECT stored_path FROM active_materials WHERE id=?", (material_id,)).fetchone()[0])
    assert stored.is_file()
    # Even a corrupted locator cannot cause account A's purge to remove B's file.
    foreign = _native_path(roots[1] / workspace / filename)
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_bytes(b"foreign account must survive")
    with active_teaching._connect() as connection:
        connection.execute("UPDATE active_materials SET stored_path=? WHERE id=?", (str(foreign), material_id))
        connection.commit()
    result = client.request("DELETE", url + "/" + material_id, json={"physical": True, "confirm_unreferenced": True})
    assert result.status_code == 200
    assert foreign.read_bytes() == b"foreign account must survive"
    assert stored.is_file()
    stored.unlink()
    # A missing payload does not make repeat archive/purge an unhandled I/O error.
    result = client.request("DELETE", url + "/" + material_id, json={"physical": True, "confirm_unreferenced": True})
    assert result.status_code in (200, 404)


def test_long_nested_exact_copy_move_and_cleanup(tmp_path):
    source_root = tmp_path / ("s" * 64) / ("a" * 64)
    destination_root = tmp_path / ("d" * 64) / ("b" * 64)
    source = _native_path(source_root / "nested" / ("n" * 100) / "资料.txt")
    source.parent.mkdir(parents=True)
    payload = b"Synthetic copy/move payload."
    source.write_bytes(payload)
    destination = destination_root / "nested" / ("m" * 100) / "资料.txt"
    copy_plain_file_exact(Path(str(source).removeprefix("\\\\?\\")), destination, expected_bytes=len(payload),
                          expected_sha256=hashlib.sha256(payload).hexdigest(),
                          source_root=source_root, destination_root=destination_root)
    copied = _native_path(destination)
    moved = copied.with_name("moved.txt")
    copied.rename(moved)
    assert moved.read_bytes() == source.read_bytes() == payload
    moved.unlink()
    assert not moved.exists() and source.exists()
