"""Exercise the publisher's actual static tree through the existing updater."""
import base64
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption, PublicFormat

from scripts import build_release as builder
from desktop.update_protocol import canonical, digest, MANIFEST_PATH, verify_release, SecurityError
from desktop.update_service import MatureUpdateService
from desktop.update_download import HTTPTransport


def package(root, version, *, target=False):
    files = {'MentorFlow.exe': b'MZ SYNTHETIC NOT A SHIPPED BINARY',
             '_internal/runtime.dll': b'unchanged runtime\0' * 8192,
             '_internal/ui.txt': b'new UI' if target else b'old UI',
             'mentorflow-version.json': canonical(dict(schema='mentorflow.version.v1', product='MentorFlow', version=version)),
             'SELF-CHECK/MentorFlowSelfCheck.exe': b'MZ synthetic helper',
             'SELF-CHECK/verify_windows_package.py': b'# synthetic',
             'SELF-CHECK/mentorflow-check.bat': b'@echo synthetic',
             'SELF-CHECK/mentorflow-update.bat': b'@echo synthetic',
             '_internal/demo.dist-info/INSTALLER': b'fixture'}
    files['added.txt' if target else 'deleted.txt'] = b'file-set changes'
    for path, data in files.items():
        item = root / path
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_bytes(data)
    manifest, _ = builder.inventory(root, version)
    (root / MANIFEST_PATH).write_bytes(canonical(manifest))
    return root


class StaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


@contextmanager
def serving(root):
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(StaticHandler, directory=str(root)))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()


@pytest.fixture
def case(tmp_path, monkeypatch):
    # Test-only ephemeral key stays in memory; never trusted by production builds.
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(builder.Ed25519PrivateKey, 'generate', lambda: key)
    base = package(tmp_path / 'base', '1.1.0')
    target = package(tmp_path / 'target', '1.1.1', target=True)
    output = tmp_path / 'release'
    notes = tmp_path / 'notes.txt'
    notes.write_text('发布说明：synthetic qualification only.\n', encoding='utf-8')
    with serving(output) as origin:
        args = builder.parser().parse_args(['--target', str(target), '--base', str(base),
            '--output', str(output), '--version', '1.1.1', '--channel', 'stable', '--notes', str(notes),
            '--origin', origin, '--source-sha', 'a'*40, '--tree-sha', 'b'*40, '--development-signing'])
        result = builder.build(args)
        yield dict(root=tmp_path, base=base, target=target, output=output, origin=origin,
                   args=args, key=key, keys={'ephemeral-development': key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)},
                   result=result)


def service(case, *, current='1.1.0', channel='stable', cache='cache', install='install'):
    root = case['root'] / install
    if not (root / 'App').exists():
        shutil.copytree(case['base'], root / 'App')
    return MatureUpdateService(install_root=root, cache_root=case['root'] / cache,
        metadata_url=case['origin'] + '/' + channel + '.json', public_keys=case['keys'],
        current=current, channel=channel, transport=HTTPTransport(case['origin'], fixture_http=True))


def resign(case, mutate):
    path = case['output'] / 'stable.json'
    document = json.loads(path.read_bytes())
    mutate(document['signed'])
    document['signature'] = base64.b64encode(case['key'].sign(canonical(document['signed']))).decode()
    path.write_bytes(canonical(document))


@pytest.mark.parametrize('fallback', [False, True])
def test_base_target_static_http_delta_and_full(case, fallback):
    client = service(case, current='1.0.0' if fallback else '1.1.0')
    original = {p.relative_to(case['base']).as_posix(): p.read_bytes() for p in case['base'].rglob('*') if p.is_file()}
    assert client.check()['state'] == 'UPDATE_AVAILABLE'
    assert client.plan['update_path'] == ('FULL_FALLBACK' if fallback else 'FILE_LEVEL_INCREMENTAL')
    result = client.download()
    assert result['state'] == 'READY_TO_APPLY'
    manifest = client.release.signed['file_manifest']
    for row in client.release.rows.values():
        path = client.staged / row['path']
        assert path.stat().st_size == row['size']
        assert digest(path.read_bytes()) == row['sha256']
    assert manifest['version'] == '1.1.1'
    assert not (client.staged / 'deleted.txt').exists()
    assert (client.staged / 'added.txt').exists()
    assert {p.relative_to(case['base']).as_posix(): p.read_bytes() for p in case['base'].rglob('*') if p.is_file()} == original
    # Full fallback deduplicates equal content hashes (e.g. identical helpers).
    downloaded = {client.release.rows[p]['sha256']: client.release.rows[p]['size'] for p in client.plan['download_paths']}
    assert result['actual_downloaded_bytes'] == sum(downloaded.values())
    if not fallback:
        assert result['actual_downloaded_bytes'] < result['full_bytes']
        assert '_internal/runtime.dll' not in client.plan['download_paths']
    client.close()


def test_corrupted_asset_fails_closed(case):
    client = service(case)
    assert client.check()['state'] == 'UPDATE_AVAILABLE'
    row = client.release.assets['_internal/ui.txt']
    path = case['output'] / 'objects' / row['sha256']
    path.write_bytes(b'X' + path.read_bytes()[1:])
    assert client.download()['state'] == 'FAILED_SECURITY'
    assert client.staged is None


@pytest.mark.parametrize('fallback', [False, True])
def test_published_feed_applies_through_existing_transaction(case, fallback):
    # Real signed bytes, copy/swap/hash/receipt; isolated ACL/registry adapters.
    # This is not a production UAC, certificate trust, or GUI launch claim.
    from scripts import windows_install_transaction as tx
    from scripts import windows_install_trust as trust
    from scripts import windows_update_apply as apply
    from scripts.test_install_process_recovery import adapters, Registry
    from scripts.test_install_transaction import Shortcut as MemoryShortcut
    root = case['root']
    install = root / 'transaction-install'
    uninstaller = root / 'Uninstall.exe'
    uninstaller.write_bytes(b'MZ fixture uninstaller')
    user_data = root / 'synthetic-user-data.txt'
    user_data.write_bytes(b'preserved independent learning data')
    ids = dict(manifest_sha256=builder.file_hash(case['base'] / MANIFEST_PATH), source_sha='a'*40, tree_sha='b'*40)
    with ExitStack() as stack:
        shortcut = MemoryShortcut()
        adapters(stack)
        stack.enter_context(patch.object(tx, 'cleanup_failed', return_value=0))
        tx.Transaction(case['base'], install, ids, uninstaller,
                       registry=Registry(root), shortcut=shortcut).run()
        if fallback:
            resign(case, lambda signed: signed.update(base_versions=[]))
        client = service(case, install='transaction-install')
        assert client.check()['state'] == 'UPDATE_AVAILABLE'
        assert client.plan['update_path'] == ('FULL_FALLBACK' if fallback else 'FILE_LEVEL_INCREMENTAL')
        assert client.download()['state'] == 'READY_TO_APPLY'
        stack.enter_context(patch.object(apply, 'PRODUCTION_RELEASE_KEYS', case['keys']))
        stack.enter_context(patch.object(tx, 'Registry', lambda: Registry(root)))
        stack.enter_context(patch.object(tx, 'ShortcutSet', lambda: shortcut))
        result = apply.apply_update(install, client.staged, client.release.envelope)
        assert result['pending_update_health'] is True
        receipt = json.loads((install / trust.RECEIPT).read_bytes())
        assert receipt['binding']['version'] == '1.1.1'
        assert json.loads((install / 'App/mentorflow-version.json').read_bytes())['version'] == '1.1.1'
        assert (install / 'App/_internal/ui.txt').read_bytes() == b'new UI'
        assert not (install / 'App/deleted.txt').exists()
        assert Path(result['previous_retained']).is_dir()
        assert user_data.read_bytes() == b'preserved independent learning data'
        client.close()


def test_tampered_metadata_fails_closed(case):
    path = case['output'] / 'stable.json'
    document = json.loads(path.read_bytes())
    document['signed']['mandatory'] = True
    path.write_bytes(canonical(document))
    assert service(case).check()['state'] == 'FAILED_SECURITY'


def test_expired_metadata_fails_closed(case):
    def expire(signed):
        now = datetime.now(timezone.utc)
        signed.update(issued_at=(now-timedelta(days=2)).isoformat(),
                      published_at=(now-timedelta(days=2)).isoformat(), expires_at=(now-timedelta(days=1)).isoformat())
    resign(case, expire)
    assert service(case).check()['state'] == 'FAILED_SECURITY'


def test_downgrade_fails_closed(case):
    assert service(case, current='9.0.0').check()['state'] == 'FAILED_SECURITY'


def test_stale_cached_update_after_restart(case):
    first = (case['output'] / 'stable.json').read_bytes()
    client = service(case)
    assert client.check()['state'] == 'UPDATE_AVAILABLE'
    assert client.download()['state'] == 'READY_TO_APPLY'
    client.close()
    args = case['args']
    args.target = package(case['root'] / 'new-target', '1.1.2', target=True)
    args.version = '1.1.2'
    result = builder.build(args)
    assert result['metadata_version'] == 2
    client = service(case)
    assert client.check()['latest'] == '1.1.2'
    client.close()
    (case['output'] / 'stable.json').write_bytes(first)
    replay = service(case)
    assert replay.check()['state'] == 'FAILED_SECURITY'
    assert replay.staged is None


def test_artifacts_and_repeat_noop(case):
    destination = Path(case['result']['release_path'])
    raw = (destination / 'release.json').read_bytes()
    assert raw == (case['output'] / 'stable.json').read_bytes()
    verified = verify_release(raw, case['keys'], current='1.1.0')
    assert verified.manifest_bytes == (destination / 'file-manifest.json').read_bytes()
    for line in (destination / 'SHA256SUMS').read_text().splitlines():
        sha, relative = line.split('  ', 1)
        assert builder.file_hash(destination / relative) == sha
    assert case['result']['PRODUCTION_SIGNING'] == 'NOT_CONFIGURED'
    assert case['result']['METADATA_SIGNING'] == 'DEVELOPMENT_ONLY'
    assert not list(case['output'].rglob('*.key')) and not list(case['output'].rglob('*.pem'))
    assert builder.build(case['args'])['status'] == 'REUSED'
    assert (case['output'] / 'stable.json').read_bytes() == raw


def test_immutable_republish_preserves_index(case):
    raw = (case['output'] / 'stable.json').read_bytes()
    (case['target'] / '_internal/ui.txt').write_bytes(b'different')
    with pytest.raises(ValueError, match='immutable version'):
        builder.build(case['args'])
    assert (case['output'] / 'stable.json').read_bytes() == raw


def test_auto_base_from_signed_previous_release(case):
    args = case['args']
    args.base = None
    args.target = package(case['root'] / 'next-target', '1.1.2', target=True)
    args.version = '1.1.2'
    assert builder.build(args)['metadata_version'] == 2
    raw = (case['output'] / 'stable.json').read_bytes()
    signed = json.loads(raw)['signed']
    assert signed['base_versions'] == ['1.1.1']
    assert '_internal/runtime.dll' not in signed['incremental_assets']
    assert builder.build(args)['status'] == 'REUSED'
    assert (case['output'] / 'stable.json').read_bytes() == raw


def test_publisher_stale_index_uses_persistent_highwater(case):
    first = (case['output'] / 'stable.json').read_bytes()
    args = case['args']
    args.base = None
    args.target = package(case['root'] / 'next-target', '1.1.2', target=True)
    args.version = '1.1.2'
    assert builder.build(args)['metadata_version'] == 2
    (case['output'] / 'stable.json').write_bytes(first)
    args.target = package(case['root'] / 'third-target', '1.1.3', target=True)
    args.version = '1.1.3'
    assert builder.build(args)['metadata_version'] == 3
    assert json.loads((case['output'] / 'stable.json').read_bytes())['signed']['base_versions'] == ['1.1.2']


def test_publisher_downgrade_and_epoch_refused(case):
    args = case['args']
    args.base = None
    args.target = package(case['root'] / 'next-target', '1.1.2', target=True)
    args.version, args.metadata_version = '1.1.2', 1
    with pytest.raises(ValueError, match='high-water'):
        builder.build(args)
    args.version, args.target, args.metadata_version = '1.0.9', case['base'], None
    with pytest.raises(ValueError, match='downgrade'):
        builder.build(args)


def test_beta_independent_channel(case):
    stable = (case['output'] / 'stable.json').read_bytes()
    args = case['args']
    args.target = package(case['root'] / 'beta', '1.2.0-beta.1', target=True)
    args.version, args.channel = '1.2.0-beta.1', 'beta'
    assert builder.build(args)['metadata_version'] == 1
    client = service(case, channel='beta', cache='beta-cache')
    assert client.check()['state'] == 'UPDATE_AVAILABLE'
    assert client.download()['state'] == 'READY_TO_APPLY'
    assert (case['output'] / 'stable.json').read_bytes() == stable


@pytest.mark.parametrize('change,match', [
    ({'version': '01.1.1'}, 'SemVer'), ({'version': '1.1.1-beta.1'}, 'prerelease'),
    ({'expiry_days': 32}, 'expiry'), ({'source_sha': 'main'}, 'exact packaged'),
    ({'origin': 'http://example.com'}, 'HTTPS origin'), ({'mandatory': True}, 'mandatory'),
])
def test_invalid_publication_refused(case, change, match):
    args = case['args']
    args.output = case['root'] / 'invalid-release'
    for field, value in change.items():
        setattr(args, field, value)
    with pytest.raises((ValueError, SecurityError), match=match):
        builder.build(args)
    assert not (args.output / 'stable.json').exists()


def test_failed_publish_recover_exact_version(case, monkeypatch):
    args = case['args']
    args.output = case['root'] / 'crash-release'
    actual = builder.atomic
    def fail_index(path, data):
        if path == args.output / 'stable.json':
            raise OSError('simulated power loss before channel replacement')
        return actual(path, data)
    monkeypatch.setattr(builder, 'atomic', fail_index)
    with pytest.raises(OSError, match='power loss'):
        builder.build(args)
    assert not (args.output / 'stable.json').exists()
    immutable = (args.output / '1.1.1/stable/release.json').read_bytes()
    monkeypatch.setattr(builder, 'atomic', actual)
    assert builder.build(args)['status'] == 'REUSED'
    assert (args.output / 'stable.json').read_bytes() == immutable


def test_missing_key_and_unavailable_certificate_fail_closed(case):
    args = case['args']
    args.output = case['root'] / 'no-key'
    args.development_signing = False
    args.origin = 'https://updates.example.test'
    with pytest.raises(ValueError, match='signing key required'):
        builder.build(args)
    args.signing_key = case['root'] / 'test-only.pem'
    args.signing_key.write_bytes(case['key'].private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    args.key_id = 'test-key'
    args.certificate_thumbprint = '0' * 40
    args.timestamp_server = 'https://timestamp.example.invalid'
    with pytest.raises(subprocess.CalledProcessError):
        builder.build(args)
    assert not (args.output / 'stable.json').exists()
    assert (case['target'] / 'MentorFlow.exe').read_bytes() == b'MZ SYNTHETIC NOT A SHIPPED BINARY'


def test_external_key_and_powershell_single_command(case):
    key_path = case['root'] / 'test-only.pem'
    key_path.write_bytes(case['key'].private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    output = case['root'] / 'cli-release'
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(Path(__file__).with_name('build_release.ps1')),
        '-Version', '1.1.1', '-Channel', 'stable', '-ReleaseNotes', str(case['args'].notes),
        '-TargetDirectory', str(case['target']), '-BaseDirectory', str(case['base']), '-OutputDirectory', str(output),
        '-Origin', 'https://updates.example.test', '-SourceSha', 'a'*40, '-TreeSha', 'b'*40,
        '-PythonPath', sys.executable, '-SigningKey', str(key_path), '-KeyId', 'test-only', '-Mandatory', '-MandatoryReason', 'security']
    first = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    assert first.returncode == 0, first.stderr
    document = json.loads((output / 'stable.json').read_bytes())
    assert document['signed']['mandatory'] is True
    assert json.loads(first.stdout)['PRODUCTION_SIGNING'] == 'NOT_CONFIGURED'
    repeated = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)['status'] == 'REUSED'


def test_private_material_and_output_overlap_refused(case):
    args = case['args']
    args.output = args.target / 'output'
    with pytest.raises(ValueError, match='overlap'):
        builder.build(args)
    args.output = case['root'] / 'private-release'
    (case['target'] / 'accidental.pem').write_text('do not publish')
    with pytest.raises(ValueError, match='private-key'):
        builder.build(args)
    assert not (args.output / 'stable.json').exists()


@pytest.mark.parametrize('name', ['build-receipt.json', 'file-manifest.json', 'SHA256SUMS'])
def test_modified_publication_receipt_or_document_refused(case, name):
    destination = Path(case['result']['release_path'])
    original_index = (case['output'] / 'stable.json').read_bytes()
    (destination / name).write_bytes((destination / name).read_bytes() + b' ')
    with pytest.raises(ValueError, match='signature rejected|document corruption'):
        builder.build(case['args'])
    assert (case['output'] / 'stable.json').read_bytes() == original_index


def test_concurrent_writer_lock_refused(case):
    lock = case['output'] / '.release-build.lock'
    lock.write_text('synthetic lock owner')
    try:
        with pytest.raises(ValueError, match='lock exists'):
            builder.build(case['args'])
        assert lock.read_text() == 'synthetic lock owner'
    finally:
        lock.unlink()


def test_authenticode_hook_wiring_with_mock_only_and_idempotent_retry(case, monkeypatch):
    """Mock transport contract, NOT real certificates or trusted Windows signing."""
    args = case['args']
    args.output = case['root'] / 'mock-signer-release'
    args.development_signing = False
    args.origin = 'https://updates.example.test'
    args.signing_key = case['root'] / 'test-only.pem'
    args.signing_key.write_bytes(case['key'].private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    args.key_id, args.certificate_thumbprint, args.timestamp_server = 'fixture', '0'*40, 'https://timestamp.example.invalid'
    calls = []
    def mock_sign(command, **kwargs):
        calls.append(command)
        root = Path(command[command.index('-PackageRoot')+1])
        assert root != case['target']
        rows = []
        for path in root.rglob('*'):
            if path.is_file() and path.suffix.lower() in {'.exe', '.dll', '.pyd'}:
                path.write_bytes(path.read_bytes() + b' MOCK SIGNATURE NOT AUTHENTICODE')
                rows.append(dict(path=path.relative_to(root).as_posix(), status='Valid', sha256=builder.file_hash(path),
                                 signer_thumbprint='0'*40, timestamp_thumbprint='1'*40))
        Path(command[command.index('-ReceiptPath')+1]).write_bytes(canonical(dict(
            PRODUCTION_SIGNING='CONFIGURED_AND_VERIFIED', signed_files=rows)))
        return subprocess.CompletedProcess(command, 0)
    monkeypatch.setattr(builder.subprocess, 'run', mock_sign)
    result = builder.build(args)
    assert len(calls) == 1
    signed = json.loads((args.output / 'stable.json').read_bytes())['signed']
    row = next(r for r in signed['file_manifest']['files'] if r['path'] == 'MentorFlow.exe')
    assert row['sha256'] != builder.file_hash(case['target'] / 'MentorFlow.exe')
    assert result['signed_files']
    assert builder.build(args)['status'] == 'REUSED'
    assert len(calls) == 1  # no second timestamp or mutation on retry
    assert (case['target'] / 'MentorFlow.exe').read_bytes() == b'MZ SYNTHETIC NOT A SHIPPED BINARY'
