"""Windows local signing/upload simulation with real subprocess pipes, no SSH."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.release import pipeline, sign_and_stage, transport
from test_release_pipeline91 import release91
from test_server_installer90 import server90


@pytest.fixture
def ssh93(tmp_path, monkeypatch):
    root = tmp_path / "simulated server's 目录 with spaces"
    real_run = subprocess.run
    calls = []
    simulator = Path(__file__).with_name('ssh_simulator93.py')

    def run(command, **kwargs):
        assert command[0] in ('ssh', 'scp'), 'No real SSH or other program allowed'
        calls.append((command, kwargs))
        return real_run([sys.executable, '-B', str(simulator), str(root), command[0],
                         *command[1:]], **kwargs)

    monkeypatch.setattr(transport.subprocess, 'run', run)
    server = transport.SSHServer(tmp_path / 'ssh work 目录', identity=tmp_path / 'dummy identity')
    return root, server, calls


def test_sign_stage93_windows_sign_upload_stdin_and_read_back(release91, ssh93, monkeypatch, capsys):
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    f = release91
    root, server, calls = ssh93
    private = f.app.parent / 'ephemeral-test-key.raw'
    private.write_bytes(f.private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    work = f.app.parent / "signing work's 中文"
    # Exercise the real operator entry point, signer, verifier, upload adapter,
    # subprocess stdin wrapper and server_ops; only SSH/SCP binaries are replaced.
    monkeypatch.setattr(sign_and_stage, 'SSHServer', lambda *args, **kwargs: server)
    monkeypatch.setattr(sys, 'argv', ['sign_and_stage.py', '--artifact', str(f.unsigned),
        '--private-key', str(private), '--public-key', str(f.key), '--work', str(work),
        '--ssh-identity', str(f.app.parent / 'dummy identity')])
    sign_and_stage.main()
    staged = root / 'app/.staging-2.0.3/stable/stable.json'
    assert staged.read_bytes() == (work / 'stable.json').read_bytes()
    assert pipeline.verified_release(staged, f.key).version == '2.0.3'
    fetched = transport.fetched(server, '2.0.3', work / 'readback.json', staged=True)
    assert fetched.read_bytes() == staged.read_bytes()
    output = capsys.readouterr()
    assert 'SIGNED_MANIFEST_STAGED' in output.out
    assert private.read_bytes().hex() not in output.out + output.err
    assert not list((root / 'transfers').iterdir())
    assert not list(root.rglob('*key*'))
    assert len(calls) == 3  # SCP manifest, SSH stage, SSH fetch.
    for command, kwargs in calls:
        assert kwargs.get('shell', False) is False
        if command[0] == 'ssh':
            assert command[-1] == 'sudo -n python3 -'
            assert len(command[-1]) < 30 and '\n' not in command[-1]
            assert 'def dispatch' in kwargs['input']
            assert private.read_bytes().hex() not in kwargs['input']
    witness = [json.loads(line) for line in (root / 'transport.jsonl').read_text(encoding='utf-8').splitlines()]
    assert [row['mode'] for row in witness] == ['scp', 'ssh', 'ssh']
    assert witness[1]['action'] == 'stage_manifest'


@pytest.mark.parametrize('mode', ['ssh', 'scp'])
def test_sign_stage93_transfer_errors_never_report_success(tmp_path, monkeypatch, mode):
    def fail(command, **kwargs):
        assert command[0] == mode
        raise subprocess.CalledProcessError(2, command, stderr='simulated transport failure')
    server = transport.SSHServer(tmp_path / 'ssh', identity=tmp_path / 'dummy')
    monkeypatch.setattr(transport.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError) as error:
        if mode == 'ssh':
            server.call('fetch', '2.0.3')
        else:
            server.upload(tmp_path / 'stable.json')
    assert error.value.returncode == 2


def test_sign_stage93_remote_python_error_propagates(ssh93):
    root, server, calls = ssh93
    with pytest.raises(subprocess.CalledProcessError) as error:
        server.call('fetch', '2.0.3')  # Missing remote manifest, actual server_ops error.
    assert 'FileNotFoundError' in error.value.stderr
    assert not (root / 'app').exists()


def test_sign_stage93_source_quotes_unicode_and_crlf_only_cross_stdin(tmp_path, monkeypatch):
    server = transport.SSHServer(tmp_path / 'ssh', identity=tmp_path / 'dummy')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        assert command[-1] == 'sudo -n python3 -'
        # Executed locally, preserving Windows subprocess Unicode/newline handling.
        return real_run([sys.executable, '-B', '-'], **kwargs)
    real_run = subprocess.run
    source = tmp_path / 'mock_ops.py'
    source.write_bytes(b'from __future__ import annotations\r\nimport json,sys\r\n'
        b'request=json.load(sys.stdin)\r\nprint(json.dumps(request, ensure_ascii=True))\r\n')
    monkeypatch.setattr(transport.server_ops, '__file__', str(source))
    monkeypatch.setattr(transport.subprocess, 'run', run)
    value = '中文 "double" and \'single\'\n$HOME; `whoami` \\path'
    result = server.call('echo', '2.0.3', note=value)
    assert result['note'] == value and len(calls) == 1
