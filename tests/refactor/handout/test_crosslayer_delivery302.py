import hashlib
import json
from pathlib import Path

import pytest

from scripts.handout.check import check_handout
from scripts.handout.crosslayer import PAGE, render, update_handout
from scripts.handout.generate import build_handout


def fixture(tmp_path, commit='a'*40):
    source = tmp_path / 'snapshot'
    source.mkdir()
    files = []
    for name in ['src/a.py', 'src/b.py']:
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_text('def run():\n    pass\n', encoding='utf-8')
        files.append(dict(path=name, blob='a'*40, mode='100644', bytes=20))
    out = tmp_path / 'handout'
    config = dict(project='fixture', groups=dict(test_roots=['tests'], categories={}), annex=dict(enabled=False))
    manifest = build_handout(source, files, dict(nodes=[], edges=[]), out, config, commit)
    for slot in manifest['slots']:
        page = out / slot['file']
        text = page.read_text(encoding='utf-8')
        marker = f'<!-- handout:todo id="{slot["id"]}" -->\n'
        page.write_text(text.replace(marker, marker + '已读源码填写的职责。\n'), encoding='utf-8')
    ep = lambda name: dict(file=name, line=1, function='run')
    result = dict(connections=[dict(kind=kind, name='fixed-name', source=ep('src/a.py'),
                                   target=ep('src/b.py'), via=[ep('src/a.py')], rule='字面量注册/路由规则')
                               for kind in ['bridge', 'http', 'dispatch', 'process']],
                  dynamic=[dict(kind='dispatch', name='getattr', source=ep('src/a.py'), reason='运行时输入')],
                  unresolved=[dict(kind='http', name='GET /missing', source=ep('src/a.py'), reason='快照中没有接收端')])
    return source, out, result


def fingerprints(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_crosslayer302_only_three_outputs_change_and_check_and_repeat(tmp_path):
    source, out, result = fixture(tmp_path)
    original = fingerprints(out)
    counts = update_handout(out, source, result, 'a'*40)
    after = fingerprints(out)
    changed = {p for p in after if original.get(p) != after[p]}
    assert changed == {PAGE, 'README.md', 'manifest.json'}
    assert counts['connections'] == dict(bridge=1, http=1, dispatch=1, process=1)
    assert check_handout(out)['ok']
    update_handout(out, source, result, 'a'*40)
    assert fingerprints(out) == after
    text = (out / PAGE).read_text(encoding='utf-8')
    assert text.index('## src/b.py') < text.index('## 动态分发点') < text.index('## 只找到一头')
    assert 'src/a.py:1' in text and 'src/b.py:1' in text
    manifest = json.loads((out / 'manifest.json').read_text(encoding='utf-8'))
    assert any(r['file'] == PAGE for r in manifest['automatic'])


def test_crosslayer302_wrong_snapshot_and_bad_line_refuse_before_writes(tmp_path):
    source, out, result = fixture(tmp_path)
    before = fingerprints(out)
    with pytest.raises(ValueError, match='快照'):
        update_handout(out, source, result, 'b'*40)
    result['connections'][0]['target']['line'] = 100
    with pytest.raises(ValueError, match='行号'):
        update_handout(out, source, result, 'a'*40)
    assert fingerprints(out) == before


def test_crosslayer302_render_is_stable_and_escapes_tables():
    ep = dict(file='a.py', line=1, function='a')
    result = dict(connections=[dict(kind='dispatch', name='x|y', source=ep, target=ep, rule='x\ny')],
                  unresolved=[], dynamic=[])
    first, hashes = render(result, 'a'*40)
    assert (first, hashes) == render(result, 'a'*40)
    assert 'x\\|y' in first


def test_crosslayer302_command_reuses_verified_archive(tmp_path, monkeypatch):
    import subprocess
    from scripts.handout import crosslayer
    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args]).decode().strip()
    git('init', '-q')
    (repo / 'src').mkdir()
    for name in ('a.py', 'b.py'):
        (repo / 'src' / name).write_text('def run():\n    pass\n', encoding='utf-8')
    git('add', '.')
    git('-c', 'user.name=test302', '-c', 'user.email=test302@example.local', 'commit', '-qm', 'fixture')
    oid = git('rev-parse', 'HEAD')
    _, out, result = fixture(tmp_path, oid)
    monkeypatch.setattr(crosslayer, 'collect', lambda source: result)
    work = tmp_path / 'work'
    crosslayer.generate_crosslayer(repo, oid, out, work, {})
    first = fingerprints(out)
    crosslayer.generate_crosslayer(repo, oid, out, work, {})
    assert fingerprints(out) == first
    (work / 'snapshot/src/a.py').write_text('mutated = True\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Git blob'):
        crosslayer.generate_crosslayer(repo, oid, out, work, {})
    assert fingerprints(out) == first


def test_crosslayer302_multiple_server_implementations_are_explicit(monkeypatch):
    import copy
    from scripts.handout import crosslayer, crosslayer_bridge, crosslayer_dispatch, crosslayer_http, crosslayer_process
    ep = lambda p: dict(file=p, line=1, function='handler')
    rows = [dict(kind='http', name='GET /same', source=ep('client.py'), target=ep(p),
                 route_declaration=ep(p), rule='literal match') for p in ('java.java', 'python.py')]
    rows.append(copy.deepcopy(rows[0]))
    for module in (crosslayer_bridge, crosslayer_dispatch, crosslayer_process):
        monkeypatch.setattr(module, 'extract', lambda _: dict(connections=[], unresolved=[], dynamic=[]))
    monkeypatch.setattr(crosslayer_http, 'extract', lambda _: dict(connections=copy.deepcopy(rows), unresolved=[], dynamic=[]))
    result = crosslayer.collect(Path('.'))
    assert len(result['connections']) == 2
    for row in result['connections']:
        assert '不代表同时调用' in row['rule']
        assert row['via'][-1] == row['route_declaration']
