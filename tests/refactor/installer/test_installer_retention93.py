"""Installer retention and reconstruction on isolated files, without SSH or Inno."""
import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from scripts.release import pipeline, server_ops
from scripts.release.transport import LocalServer
from test_server_installer90 import server90


@pytest.fixture
def retention93(tmp_path):
    server = LocalServer(tmp_path)
    releases = server.app / 'releases'
    releases.mkdir(parents=True)

    def seed(v, *, manifest=None):
        directory = releases / v
        directory.mkdir()
        raw = manifest if manifest is not None else json.dumps({'version': v}).encode()
        (directory / 'stable.json').write_bytes(raw)
        exe = directory / ('MentorFlow-Setup-' + v + '.exe')
        exe.write_bytes(('MZ fixture ' + v).encode())
        return exe

    def latest(v):
        target = releases / 'latest/MentorFlow-Setup.exe'
        target.parent.mkdir(exist_ok=True)
        os.link(releases / v / ('MentorFlow-Setup-' + v + '.exe'), target)
        return target

    def publish(v, *, switch=False, data=None, manifest=None, bad_hash=False):
        exe = releases / v / ('MentorFlow-Setup-' + v + '.exe')
        data = exe.read_bytes() if data is None else data
        source = tmp_path / ('transfer-' + uuid.uuid4().hex)
        source.write_bytes(data)
        raw = (releases / v / 'stable.json').read_bytes() if manifest is None else manifest
        return server.call('installer', v, transfer=str(source), latest=switch,
                           manifest=base64.b64encode(raw).decode(), installer={
                               'size': len(data), 'sha256': '0' * 64 if bad_hash else hashlib.sha256(data).hexdigest()})

    return SimpleNamespace(server=server, releases=releases, seed=seed, latest=latest, publish=publish)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_retention93_five_versions_prune_two_keep_manifests_objects_and_repeat(retention93, capsys):
    f = retention93
    exes = [f.seed('2.0.' + str(n)) for n in range(1, 6)]
    manifests = {p.parent.name: (p.parent / 'stable.json').read_bytes() for p in exes}
    objects = f.server.app / 'objects'
    objects.mkdir()
    (objects / 'unchanged-object').write_bytes(b'object bytes')
    unknown = f.releases / 'other-files'
    unknown.mkdir()
    (unknown / 'leave.exe').write_bytes(b'not a numeric release')
    f.latest('2.0.5')
    result = f.publish('2.0.5', switch=True)['retention']
    assert result['keep_count'] == server_ops.INSTALLER_KEEP_COUNT == 3
    assert {r['version'] for r in result['deleted']} == {'2.0.1', '2.0.2'}
    assert result['removed_bytes'] == result['released_bytes'] == sum(len(('MZ fixture 2.0.' + str(n)).encode()) for n in (1, 2))
    assert [p.exists() for p in exes] == [False, False, True, True, True]
    for v, raw in manifests.items():
        assert (f.releases / v).is_dir() and (f.releases / v / 'stable.json').read_bytes() == raw
    assert snapshot(objects) == {'unchanged-object': b'object bytes'}
    assert (unknown / 'leave.exe').read_bytes() == b'not a numeric release'
    output = capsys.readouterr()
    assert output.out == ''  # Remote adapter reserves stdout for its JSON response.
    logs = [json.loads(line) for line in output.err.splitlines()]
    assert logs == result['events']
    assert [row['event'] for row in logs].count('installer_retention_before_delete') == 2
    assert [row['event'] for row in logs].count('installer_retention_after_delete') == 2
    before = snapshot(f.releases)
    repeat = f.publish('2.0.5', switch=True)['retention']
    assert repeat['deleted'] == [] and repeat['released_bytes'] == repeat['removed_bytes'] == 0
    assert snapshot(f.releases) == before


def test_retention93_numeric_sort_not_lexical(retention93):
    f = retention93
    versions = ['2.0.2', '2.0.9', '2.0.10', '2.1.0', '10.0.0']
    exes = [f.seed(v) for v in versions]
    result = f.publish('10.0.0', switch=True)['retention']
    assert {r['version'] for r in result['deleted']} == {'2.0.2', '2.0.9'}
    assert [p.exists() for p in exes] == [False, False, True, True, True]


def test_retention93_old_latest_protected_by_file_identity(retention93):
    f = retention93
    exes = [f.seed('2.0.' + str(n)) for n in range(1, 6)]
    latest = f.latest('2.0.1')
    result = f.publish('2.0.5')['retention']
    assert result['protected_latest'] == ['2.0.1']
    assert [r['version'] for r in result['deleted']] == ['2.0.2']
    assert [p.exists() for p in exes] == [True, False, True, True, True]
    assert os.path.samefile(latest, exes[0])


@pytest.mark.parametrize('count', [1, 2, 3])
def test_retention93_up_to_three_no_deletion(retention93, count):
    f = retention93
    for n in range(1, count + 1):
        f.seed('2.0.' + str(n))
    before = snapshot(f.releases)
    result = f.publish('2.0.' + str(count))['retention']
    assert result['deleted'] == [] and result['removed_bytes'] == result['released_bytes'] == 0
    assert snapshot(f.releases) == before


@pytest.mark.parametrize('fault', ['hash', 'metadata', 'latest'])
def test_retention93_no_pruning_before_success(retention93, monkeypatch, fault):
    f = retention93
    for n in range(1, 6):
        f.seed('2.0.' + str(n))
    f.latest('2.0.5')
    before = snapshot(f.releases)
    if fault == 'latest':
        monkeypatch.setattr(server_ops.os, 'link', lambda *args: (_ for _ in ()).throw(OSError('link failed')))
    with pytest.raises((ValueError, OSError)):
        f.publish('2.0.5', switch=True, bad_hash=fault == 'hash',
                  manifest=b'changed metadata' if fault == 'metadata' else None)
    assert snapshot(f.releases) == before


def test_retention93_released_bytes_do_not_count_other_hardlinks(retention93):
    f = retention93
    exes = [f.seed('2.0.' + str(n)) for n in range(1, 6)]
    alias = f.server.root / 'retained-copy.exe'
    os.link(exes[1], alias)
    result = f.publish('2.0.5')['retention']
    rows = {r['version']: r for r in result['deleted']}
    assert rows['2.0.2']['released_bytes'] == 0
    assert result['released_bytes'] == rows['2.0.1']['bytes']
    assert result['removed_bytes'] == sum(r['bytes'] for r in rows.values())
    assert alias.read_bytes() == b'MZ fixture 2.0.2'


def test_retention93_delete_failure_logged_without_false_completion(retention93, monkeypatch, capsys):
    f = retention93
    for n in range(1, 6):
        f.seed('2.0.' + str(n))
    target = f.releases / '2.0.2/MentorFlow-Setup-2.0.2.exe'
    original = Path.unlink
    def fail(path, *args, **kw):
        if path == target:
            raise PermissionError('fixture locked file')
        return original(path, *args, **kw)
    monkeypatch.setattr(Path, 'unlink', fail)
    with pytest.raises(PermissionError, match='fixture locked file'):
        f.publish('2.0.5')
    events = [json.loads(line)['event'] for line in capsys.readouterr().err.splitlines()]
    assert events == ['installer_retention_before_delete', 'installer_retention_delete_failed']
    assert target.exists()


@pytest.mark.parametrize('make_latest', [False, True])
def test_retention93_pipeline_rebuilds_manifest_only_version(server90, tmp_path, make_latest):
    f = server90
    server = LocalServer(tmp_path)
    releases = f.app / 'releases'
    for n in range(3, 8):
        directory = releases / ('2.0.' + str(n))
        directory.mkdir(parents=True)
        (directory / 'stable.json').write_bytes(f.stable.read_bytes() if n == 3 else b'newer metadata fixture')
        (directory / ('MentorFlow-Setup-2.0.' + str(n) + '.exe')).write_bytes(b'previous fixture exe')
    server_ops.retain_installers(releases)
    restored = releases / '2.0.3/MentorFlow-Setup-2.0.3.exe'
    assert not restored.exists()
    raw = (releases / '2.0.3/stable.json').read_bytes()
    objects_before = snapshot(f.objects)
    def compile_fixture(args):
        args.output.mkdir(parents=True)
        (args.output / 'MentorFlow-Setup.exe').write_bytes(b'MZ rebuilt fixture93')
    result = pipeline.installer(server, '2.0.3', tmp_path / 'rebuild', Path('fixture-ISCC'),
                                public_key=f.key, latest=make_latest, compile_fn=compile_fixture)
    assert result['publish']['status'] == 'INSTALLER_PUBLISHED'
    assert (releases / '2.0.3/stable.json').read_bytes() == raw
    assert snapshot(f.objects) == objects_before
    if make_latest:
        assert restored.read_bytes() == b'MZ rebuilt fixture93'
        assert os.path.samefile(restored, releases / 'latest/MentorFlow-Setup.exe')
        assert result['publish']['retention']['protected_latest'] == ['2.0.3']
    else:
        assert not restored.exists()  # Same explicit policy applies after rebuilding.
        assert [r['version'] for r in result['publish']['retention']['deleted']] == ['2.0.3']
    assert not list((releases / '2.0.3').glob('.MentorFlow-Setup-*'))


def test_retention93_restore_rejects_changed_metadata(retention93):
    f = retention93
    exe = f.seed('2.0.1')
    exe.unlink()
    with pytest.raises(ValueError, match='different signed metadata'):
        f.publish('2.0.1', data=b'MZ rebuilt', manifest=b'different manifest')
    assert not exe.exists()
    assert {p.name for p in exe.parent.iterdir()} == {'stable.json'}
