"""Reuse actual signed-target content regardless of publisher delta base hints."""
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

from refactor.desktop.delivery import update_download as io
from refactor.desktop.delivery.update_protocol import MANIFEST_PATH, digest
from test_delivery_mature_update import feed


def test_old_version100_not_in_delta_base_reuses_verified_bytes(feed):
    feed.signed['base_versions'] = ['0.9.0']
    feed.signed['incremental_assets'] = []
    feed.sign(feed.signed)
    service = feed.service()
    checked = service.check()
    assert checked['update_path'] == 'FILE_LEVEL_INCREMENTAL'
    result = service.download()
    assert result['state'] == 'READY_TO_APPLY'
    assert result['actual_downloaded_bytes'] == checked['expected_download_bytes']
    assert result['actual_downloaded_bytes'] < result['full_bytes']/100


def test_old_version100_no_local_manifest_still_hashes_existing_app(feed):
    (feed.app/MANIFEST_PATH).unlink()
    service = feed.service()
    assert service.check()['update_path'] == 'FULL_FALLBACK'
    result = service.download()
    assert result['state'] == 'READY_TO_APPLY'
    assert result['update_path'] == 'FILE_LEVEL_INCREMENTAL'
    assert result['actual_downloaded_bytes'] == result['expected_download_bytes']
    assert result['actual_downloaded_bytes'] < result['full_bytes']/100
    assert (service.staged/'MentorFlow.exe').read_bytes() == feed.old_files['MentorFlow.exe']


def test_old_version100_equal_size_tamper_is_downloaded_not_reused(feed):
    service = feed.service()
    checked = service.check()
    source = feed.app/'MentorFlow.exe'
    original = source.read_bytes()
    source.write_bytes(b'X'*len(original))
    result = service.download()
    assert result['state'] == 'READY_TO_APPLY'
    assert result['expected_download_bytes'] == checked['expected_download_bytes'] + len(original)
    assert result['actual_downloaded_bytes'] == result['expected_download_bytes']
    assert (service.staged/'MentorFlow.exe').read_bytes() == original
    assert source.read_bytes() == b'X'*len(original)


def test_old_version100_already_new_bytes_beat_stale_manifest(feed):
    service = feed.service()
    checked = service.check()
    path = '_internal/frontend/ui.txt'
    newest = b'new frontend\n'
    assert digest(newest) == service.release.rows[path]['sha256']
    (feed.app/path).write_bytes(newest)
    result = service.download()
    assert result['state'] == 'READY_TO_APPLY'
    assert result['expected_download_bytes'] == checked['expected_download_bytes'] - len(newest)
    assert result['actual_downloaded_bytes'] == result['expected_download_bytes']
    assert (service.staged/path).read_bytes() == newest


def test_old_version100_cancel_during_hashing_sends_no_asset_requests(feed):
    service = feed.service()
    service.check()
    before = len(feed.requests)
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls >= 3
    assert service.download(cancel=cancel)['state'] == 'DOWNLOAD_PAUSED_OR_INTERRUPTED'
    assert len(feed.requests) == before
    assert service.staged is None


def test_old_version100_real_205_to_209_manifest_inventory(tmp_path, monkeypatch):
    # Complete file inventories from the two public, signature-verified releases.
    # Simulate old bytes by their known hashes; never allocate a 1 GiB dummy App.
    fixture = json.loads(gzip.decompress((Path(__file__).parent/'data/old_versions100.json.gz').read_bytes()))
    old = fixture['versions']['2.0.5']['rows']
    target = fixture['versions']['2.0.9']
    rows = target['rows']
    assert len(old) == len(rows) == 4120
    assert target['base_versions'] == ['2.0.8']
    release = SimpleNamespace(version='2.0.9', rows=rows, signed={
        'full_release': {'size': sum(r['size'] for r in rows.values())},
        'base_versions': target['base_versions'], 'incremental_assets': target['incremental_assets'],
        'channel': target['channel'], 'release_notes': target['release_notes']})
    app = tmp_path/'App'
    original_stat = Path.stat
    checked = []
    def row_of(path):
        return old.get(Path(path).relative_to(app).as_posix())
    def fake_stat(path, *args, **kwargs):
        if path.is_relative_to(app):
            row = row_of(path)
            if row is None: raise FileNotFoundError(path)
            return SimpleNamespace(st_size=row['size'])
        return original_stat(path, *args, **kwargs)
    def fake_hash(path):
        checked.append(Path(path).relative_to(app).as_posix())
        return row_of(path)['sha256']
    monkeypatch.setattr(io, 'safe_entry', lambda p, root: row_of(p) is not None)
    monkeypatch.setattr(io, 'hash_file', fake_hash)
    monkeypatch.setattr(Path, 'stat', fake_stat)
    cache = object.__new__(io.ObjectCache)
    plan = cache.local_file_plan(release, app, '2.0.5')
    assert plan['update_path'] == 'FILE_LEVEL_INCREMENTAL'
    assert len(plan['download_paths']) == 17
    assert plan['expected_download_bytes'] == 25_678_111
    assert plan['full_bytes'] == 1_075_805_550
    assert len(checked) >= 4100  # Size differences need no hash; others MUST be read.
    same = next(p for p in checked if p not in plan['download_paths'])
    changed_hash = old[same]['sha256']
    old[same] = {**old[same], 'sha256': '0'*64 if changed_hash != '0'*64 else '1'*64}
    altered = cache.local_file_plan(release, app, '2.0.5')
    assert same in altered['download_paths']
    assert altered['expected_download_bytes'] == 25_678_111 + rows[same]['size']
