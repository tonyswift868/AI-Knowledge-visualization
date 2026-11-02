"""Metadata-only map, conservative source evidence and bounded existing-map query."""
from pathlib import Path
import importlib.util
import json
import sqlite3

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / 'scripts/project_map/data_map.py'
spec = importlib.util.spec_from_file_location('project_data_map_tested', SCRIPT)
mapper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mapper)


def test_metadata_accounts_and_privacy(tmp_path, monkeypatch):
    app, data, local = (tmp_path / n for n in ('app', 'data', 'local'))
    for directory in (app / 'config', data, local / 'test-tmp', local / 'release-keys'):
        directory.mkdir(parents=True)
    (app / 'config/settings.json').write_text('{"theme":"VALUE_SENTINEL"}', encoding='utf-8')
    for name in ('auth.json', 'private_KEY.json', 'password.txt', 'notes.txt', 'plan.json'):
        (data / name).write_text('{"body":"BODY_SENTINEL"}', encoding='utf-8')
    real_connect = sqlite3.connect
    for identifier in ('abcd-account-one', 'efgh-account-two'):
        account = data / 'learning/accounts' / identifier
        account.mkdir(parents=True)
        (account / 'settings.json').write_text('{"body":"LEARNING_SENTINEL"}', encoding='utf-8')
        with real_connect(account / 'course.sqlite3') as db:
            db.execute("CREATE TABLE material(id TEXT UNIQUE, body TEXT DEFAULT 'DEFAULT_SENTINEL')")
            db.execute('INSERT INTO material VALUES (?,?)', (identifier, 'ROW_SENTINEL'))
            db.commit()
        db.close()
    statements = []

    def schema_only(database_uri, **kwargs):
        assert database_uri.startswith('file:') and 'mode=ro' in database_uri and 'immutable=1' in database_uri and kwargs['uri']
        db = real_connect(database_uri, **kwargs)
        db.set_trace_callback(statements.append)

        def authorize(action, table, column, database, origin):
            if action == sqlite3.SQLITE_READ and table not in {'sqlite_master', 'pragma_table_info', 'pragma_index_info'}:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        return db

    opened = []
    actual_open = Path.open

    def record_open(path, *args, **kwargs):
        opened.append(path)
        return actual_open(path, *args, **kwargs)
    actual_scandir = mapper.os.scandir

    def shallow_local(path):
        assert not Path(path).is_relative_to(local / 'test-tmp')
        assert not Path(path).is_relative_to(local / 'release-keys')
        return actual_scandir(path)
    monkeypatch.setattr(mapper.sqlite3, 'connect', schema_only)
    monkeypatch.setattr(Path, 'open', record_open)
    monkeypatch.setattr(mapper.os, 'scandir', shallow_local)
    monkeypatch.setattr(mapper, '_main_head', lambda: 'fixture-head')
    report = mapper.scan([app, data], local)
    encoded = json.dumps(report, ensure_ascii=False)
    for forbidden in ('VALUE_SENTINEL', 'BODY_SENTINEL', 'ROW_SENTINEL', 'DEFAULT_SENTINEL', 'LEARNING_SENTINEL', 'abcd-account-one', 'efgh-account-two'):
        assert forbidden not in encoded
    assert opened == [app / 'config/settings.json']
    assert all(not p.name.endswith(('-wal', '-shm')) for p in data.rglob('*'))
    findings = report['metadata_findings']
    schema = next(x for x in findings if x['kind'] == 'sqlite_schema')
    assert schema['instance_count'] == 2
    assert schema['schema']['tables'][0]['columns'][1]['name'] == 'body'
    assert schema['schema']['indexes']
    assert all('SELECT * FROM material' not in query for query in statements)
    account_template = report['roots'][1]['children'][0]['children'][0]['children'][0]
    assert account_template['account_count'] == 2
    assert account_template['templates'][0]['stats']['file_count'] == 4
    assert '表 `material`' in mapper._markdown(report)


def test_pyw_and_conservative_read_write_evidence(tmp_path):
    (tmp_path / 'source_start.pyw').write_text("def start():\n    Path('source-stderr.log').write_text('message')\n", encoding='utf-8')
    (tmp_path / 'db.py').write_text('def save():\n    db.execute("CREATE INDEX idx ON calls(id)")\n', encoding='utf-8')
    evidence = mapper._source_references({'files': [{'name': 'source-stderr.log'}], 'tables': [{'name': 'calls'}]}, tmp_path)['evidence']
    assert any(hit['source'] == 'source_start.pyw' and hit['role'] == '写入调用' for item in evidence for hit in item['hits'])
    assert any(hit['source'] == 'db.py' and hit['role'] == '写入调用' for item in evidence for hit in item['hits'])
    assert mapper._operation('db.execute(query)', 'f')[0] == '数据库调用（方向未确定）'
    assert '写入' not in mapper._operation('name.replace("a", "b")', 'f')[0]
    assert '非文件' in mapper._operation('json.dumps(calls)', 'f')[0]
    assert '非正文' in mapper._operation('path.exists()', 'f')[0]


def test_cli_defaults_overrides_and_query_does_not_rescan(tmp_path, monkeypatch, capsys):
    assert mapper.MAIN_REPO == SCRIPT.parents[2]
    app, data, local, out = (tmp_path / n for n in ('app', 'data', 'local', 'output'))
    for directory in (app, data, local):
        directory.mkdir()
    monkeypatch.setattr(mapper, '_main_head', lambda: 'fixture-head')
    monkeypatch.setattr(mapper, 'enrich_owners', lambda report, source: report)
    assert mapper.main(['--app-root', str(app), '--data-root', str(data), '--local-root', str(local), '--out', str(out)]) == 0
    assert (out / 'data_map.json').is_file()
    capsys.readouterr()
    monkeypatch.setattr(mapper, 'scan', lambda *args: pytest.fail('query must not rescan data'))
    assert mapper.main(['--out', str(out), '--query', 'fixture-head']) == 0
    assert 'fixture-head' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        mapper.main(['--out', str(data / 'output'), '--app-root', str(app), '--data-root', str(data)])


def test_query_streams_bounded_context_paths_and_missing(tmp_path):
    target = tmp_path / 'data_map.md'
    with target.open('w', encoding='utf-8') as stream:
        stream.write('# 地图\n- 生成时间：test-time\n- 主仓库提交：abc123\n\n## SQLite\n')
        for number in range(3000):
            stream.write(f'- other-{number}\n')
        stream.write('- 表 `calls`：`id` TEXT\n  - 来源 usage.py:217\n')
        stream.write('- ' + 'x' * 1500 + 'D:/Preview/Data/course.sqlite3' + 'x' * 1500 + '\n')
    result = mapper.query_map(target, 'calls', limit=1, max_bytes=700)
    assert 'test-time' in result and 'abc123' in result and '表 `calls`' in result
    assert 'usage.py:217' in result and len(result.encode('utf-8')) <= 700
    path_result = mapper.query_map(target, r'D:\Preview\Data\course.sqlite3')
    assert 'D:/Preview/Data/course.sqlite3' in path_result
    assert '没有匹配' in mapper.query_map(target, 'missing-value')
    assert len(mapper.query_map(target, '😄' * 1000, max_bytes=512).encode('utf-8')) <= 512
    with pytest.raises(ValueError):
        mapper.query_map(target, '', limit=0)


def test_output_links_and_malformed_json(tmp_path, monkeypatch):
    assert mapper._output_dir(tmp_path / 'custom') == (tmp_path / 'custom').resolve()
    original = tmp_path / 'original'
    original.write_text('unchanged', encoding='utf-8')
    out = tmp_path / 'out'
    out.mkdir()
    import os
    os.link(original, out / 'data_map.json')
    with pytest.raises(ValueError, match='硬链接'):
        mapper._output_dir(out)
    assert original.read_text(encoding='utf-8') == 'unchanged'
    monkeypatch.setattr(mapper, '_is_reparse', lambda path: True)
    with pytest.raises(ValueError):
        mapper._output_dir(out)
    broken = tmp_path / 'settings.json'
    broken.write_text('{"a": [', encoding='utf-8')
    with pytest.raises(ValueError):
        mapper._stream_json_top_keys(broken)


def test_query_console_emits_utf8_even_with_windows_legacy_encoding(tmp_path):
    import os
    import subprocess
    import sys
    (tmp_path / 'data_map.md').write_text('# 图\n- 生成时间：now\n- 主仓库提交：hash\n## 结构\n- 表 `calls`：字段\n', encoding='utf-8')
    env = {**os.environ, 'PYTHONIOENCODING': 'cp936', 'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--out', str(tmp_path), '--query', 'calls'], capture_output=True, env=env, check=True)
    assert '数据地图查询：calls' in result.stdout.decode('utf-8')
    assert '表 `calls`' in result.stdout.decode('utf-8')
