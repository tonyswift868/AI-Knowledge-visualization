"""Snapshot, grouping and writing checks for the DOC-02 handout."""
from pathlib import Path
import subprocess

import pytest

from scripts.handout.check import check_handout
from scripts.handout.generate import build_handout, page_name, summarize_graph
from scripts.handout.snapshot import prepare_snapshot, verify_snapshot


def fixture_handout(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    names = ['src/a.py', 'src/b.py', 'src/icon.png', 'src/data.json', 'tests/unit/test_a.py']
    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('def run():\n    pass\n', encoding='utf-8')
    files = [dict(path=n, blob=str(i) * 40, mode='100644', bytes=(source / n).stat().st_size) for i, n in enumerate(names)]
    nodes = [dict(id='a', label='run', source_file='src/a.py', source_location='L1', _callable=True),
             dict(id='b', label='execute', source_file='src/b.py', source_location='L7', _callable=True)]
    graph = dict(nodes=nodes, edges=[dict(source='a', target='b', relation='calls', confidence='EXTRACTED', source_file='src/a.py', source_location='L3'),
        dict(source='b', target='a', relation='uses', confidence='INFERRED', source_file='src/b.py')])
    config = dict(project='fixture', groups=dict(test_roots=['tests'], categories={'资源': ['.png', '.json']}), annex=dict(enabled=False))
    out = tmp_path / 'out'
    manifest = build_handout(source, files, graph, out, config, 'a' * 40, generated_at='2026-11-02T20:00:00+09:00')
    return out, manifest


def test_handout293_grouping_calls_and_empty_skeleton(tmp_path):
    out, manifest = fixture_handout(tmp_path)
    assert manifest['counts']['single'] == 2
    assert manifest['counts']['grouped'] == 2
    assert manifest['counts']['calls'] == 1
    assert manifest['counts']['rejected'] == {'INFERRED': 1}
    assert len(manifest['groups']) == 4
    result = check_handout(out)
    assert not result['ok'] and not result['errors']
    assert len(result['unfilled']) == len(manifest['slots'])
    assert check_handout(out, allow_unfilled=True)['ok']
    text = (out / '02-调用关系/src.md').read_text(encoding='utf-8')
    assert 'src/a.py:3' in text and 'src/b.py:7' in text
    assert 'run' in text and 'execute' in text


def test_handout293_fill_slots_annotations_and_detect_auto_tamper(tmp_path):
    out, manifest = fixture_handout(tmp_path)
    for spec in manifest['slots']:
        path = out / spec['file']
        text = path.read_text(encoding='utf-8')
        marker = f'<!-- handout:todo id="{spec["id"]}" -->\n'
        path.write_text(text.replace(marker, marker + '这是已填写的职责。\n'), encoding='utf-8')
    assert check_handout(out)['ok']
    page = out / '01-文件/src.md'
    with page.open('a', encoding='utf-8') as f:
        f.write('\n> 【dots 批注｜2026-11-02｜abcdef12】学到这里。\n')
    assert check_handout(out)['ok']
    page.write_text(page.read_text(encoding='utf-8').replace('src/a.py', 'src/changed.py'), encoding='utf-8')
    assert any('哈希改变' in e for e in check_handout(out)['errors'])


def test_handout293_bad_dots_and_missing_slot_and_full_json(tmp_path):
    out, manifest = fixture_handout(tmp_path)
    spec = manifest['slots'][0]
    page = out / spec['file']
    text = page.read_text(encoding='utf-8')
    page.write_text(text.replace(f'<!-- handout:todo id="{spec["id"]}" -->', '') + '\n【dots 批注｜bad】fake\n', encoding='utf-8')
    errors = check_handout(out, allow_unfilled=True)['errors']
    assert any('缺少空位' in e for e in errors)
    assert any('dots' in e for e in errors)
    (out / '02-调用关系/calls.json').write_text('{}', encoding='utf-8')
    assert any('自动文件' in e for e in check_handout(out)['errors'])


def test_handout293_scope_uses_archive_not_dirty_workspace(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()
    git('init', '-q')
    (repo / 'source.py').write_text('original = True\n', encoding='utf-8')
    (repo / '.local').mkdir()
    (repo / '.local/hidden.txt').write_text('must not archive\n', encoding='utf-8')
    git('add', '.')
    git('-c', 'user.name=handout-test', '-c', 'user.email=handout@test.local', 'commit', '-qm', 'fixture')
    oid = git('rev-parse', 'HEAD')
    (repo / 'source.py').write_text('dirty = True\n', encoding='utf-8')
    actual, source, files, excluded = prepare_snapshot(repo, oid, tmp_path / 'build', ['.local/**'])
    assert actual == oid and [r['path'] for r in files] == ['source.py']
    assert (source / 'source.py').read_text(encoding='utf-8') == 'original = True\n'
    assert not (source / '.local').exists()
    assert len(excluded) == 1
    assert (repo / 'source.py').read_text(encoding='utf-8') == 'dirty = True\n'
    verify_snapshot(source, files)
    (source / 'source.py').write_text('changed = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Git blob'):
        verify_snapshot(source, files)


def test_handout293_definition_line_fallback_and_filename_collision(tmp_path):
    graph = dict(nodes=[dict(id='a', label='a', source_file='a.py', source_location='L2'),
                             dict(id='b', label='b', source_file='b.py', source_location='L3')],
                 edges=[dict(source='a', target='b', relation='calls', confidence='EXTRACTED')])
    summary = summarize_graph(graph, tmp_path, {'a.py', 'b.py'})
    assert summary['calls'][0]['line_kind'] == 'definition'
    assert summary['calls'][0]['source_line'] == 2
    assert page_name('a/b') != page_name('a__b')
    assert page_name('.') != page_name('_root')


def test_handout293_archive_drains_padding_before_wait(tmp_path, monkeypatch):
    import io
    import tarfile
    from scripts.handout import snapshot

    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode='w') as archive:
        member = tarfile.TarInfo('file.py')
        member.size = 4
        archive.addfile(member, io.BytesIO(b'pass'))
    raw = payload.getvalue() + b'\0' * 200000
    calls = []

    class ArchiveProcess:
        def __init__(self, *_args, **_kwargs):
            self.stdout = io.BytesIO(raw)

        def wait(self):
            # A real Windows writer cannot exit while this padding fills its pipe.
            assert self.stdout.tell() == len(raw)
            calls.append('drained')
            return 0

        def poll(self):
            return 0

    def git_fixture(_repo, *args, **_kwargs):
        if args[0] == 'rev-parse':
            return b'a' * 40
        if args[0] == 'ls-files':
            return b'100644 ' + b'b' * 40 + b' 0\tfile.py\0'
        return b''

    monkeypatch.setattr(snapshot, 'git', git_fixture)
    monkeypatch.setattr(snapshot.subprocess, 'Popen', ArchiveProcess)
    _, source, _, _ = prepare_snapshot(tmp_path, 'a' * 40, tmp_path / 'build', [])
    assert (source / 'file.py').read_bytes() == b'pass'
    assert calls == ['drained']
