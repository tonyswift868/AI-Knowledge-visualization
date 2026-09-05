"""Focused external-config boundary tests; never import desktop or user DBs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import verify_windows_package as verifier
from scripts.enterprise import prepare_account_core_package as preparation

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / 'scripts/enterprise/start_account_core_package.ps1'


def run_config(path: Path) -> subprocess.CompletedProcess:
    powershell = shutil.which('powershell.exe')
    if not powershell:
        pytest.skip('Windows PowerShell is required')
    source = TEMPLATE.read_text(encoding='utf-8')
    functions = source[source.index('function Assert-NoAlias'):source.index('\ntry {')]
    quoted = str(path).replace("'", "''")
    return subprocess.run([
        powershell, '-NoProfile', '-NonInteractive', '-Command',
        "$ErrorActionPreference='Stop'; " + functions +
        "\ntry { Read-RuntimeProfile '" + quoted + "' } catch { exit 2 }",
    ], capture_output=True, text=True, timeout=15)


@pytest.mark.parametrize('profile', ['default', 'new-device-2'])
@pytest.mark.parametrize('reverse', [False, True])
def test_legal_external_runtime_configuration(tmp_path, profile, reverse):
    payload = {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': profile}
    if reverse:
        payload = dict(reversed(list(payload.items())))
    config = tmp_path / 'launch.json'
    config.write_text(json.dumps(payload), encoding='utf-8')
    result = run_config(config)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == profile


@pytest.mark.parametrize('payload', [
    {'schema': 'wrong', 'data_profile': 'default'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': ''},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': '../formal'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'E:\\MentorFlowData'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'A' * 41},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 1},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': None},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': {}},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': []},
    {'Schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default', 'issuer': 'https://evil.test'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default', 'workspace': 'tenant-a'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default', 'organization': 'tenant-a'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default', 'tls_verify': False},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default', 'password': 'test-only'},
    {'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default', 'token': 'test-only'},
    {'environment': {'MENTORFLOW_DESKTOP_WORKSPACE_ID': 'tenant-a'}},
    [], None,
])
def test_invalid_configuration_fails_closed(tmp_path, payload):
    config = tmp_path / 'launch.json'
    config.write_text(json.dumps(payload), encoding='utf-8')
    assert run_config(config).returncode == 2


@pytest.mark.parametrize('raw', [
    '{"schema":"mentorflow.desktop-runtime.v1","schema":"mentorflow.desktop-runtime.v1"}',
    '{"data_profile":"default","data_profile":"default"}',
    '{"schema":"mentorflow.desktop-runtime.v1","data_profile":"default"} trailing',
    '{"schema":"mentorflow.desktop-runtime.v1","data_profile":"def\\u0061ult"}',
    'x' * 1025,
])
def test_malformed_duplicate_or_oversized_configuration(tmp_path, raw):
    config = tmp_path / 'launch.json'
    config.write_text(raw, encoding='utf-8')
    assert run_config(config).returncode == 2


def fake_package(tmp_path):
    package = tmp_path / 'MentorFlow'
    check = package / 'SELF-CHECK'
    check.mkdir(parents=True)
    (package / 'MentorFlow.exe').write_bytes(b'program-original')
    (package / 'Qt6Core.dll').write_bytes(b'library-original')
    (package / verifier.VERSION_FILE_NAME).write_text(json.dumps({
        'schema': verifier.VERSION_SCHEMA, 'product': 'MentorFlow', 'version': '1.2.3',
    }), encoding='utf-8')
    for name in ('MentorFlowSelfCheck.exe', 'verify_windows_package.py', 'mentorflow-check.bat', 'mentorflow-update.bat'):
        (check / name).write_bytes(b'support-original')
    return package


def test_exact_original_rejection_rule(tmp_path):
    package = fake_package(tmp_path)
    sidecar = package / 'account-core-launch.json'
    sidecar.write_text('{"environment": {}}', encoding='utf-8')
    assert verifier._find_user_data_files(package) == ['account-core-launch.json']
    with pytest.raises(verifier.PackageError, match='account-core-launch.json'):
        verifier.build_manifest(package)
    assert not (package / verifier.MANIFEST_RELATIVE).exists()


@pytest.mark.parametrize('filename', ['MentorFlow.exe', 'Qt6Core.dll', 'SELF-CHECK/MentorFlowSelfCheck.exe'])
def test_immutable_program_tampering_still_rejected(tmp_path, filename):
    package = fake_package(tmp_path)
    verifier.build_manifest(package)
    # Same-size mutation requires hash validation, not just a size comparison.
    target = package / filename
    original = target.read_bytes()
    target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(verifier.PackageError, match='tampered:'):
        verifier.verify_package(package, data_root=None, check_data=False, smoke=False, smoke_seconds=0)


def test_external_mutable_config_does_not_change_immutable_manifest(tmp_path):
    package = fake_package(tmp_path)
    manifest = verifier.build_manifest(package)
    original = manifest.read_bytes()
    runtime = tmp_path / 'Runtime'
    runtime.mkdir()
    config = runtime / 'launch.json'
    for profile in ('default', 'second-device'):
        config.write_text(json.dumps({'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': profile}), encoding='utf-8')
        assert run_config(config).returncode == 0
        verifier.verify_package(package, data_root=None, check_data=False, smoke=False, smoke_seconds=0)
        assert manifest.read_bytes() == original


def test_launcher_cleans_inherited_identity_tls_and_python_settings(tmp_path):
    source = TEMPLATE.read_text(encoding='utf-8')
    functions = source[source.index('function Assert-NoAlias'):source.index('\ntry {')]
    env = dict(os.environ, MENTORFLOW_DESKTOP_WORKSPACE_ID='wrong-tenant',
               MENTORFLOW_DESKTOP_OIDC_ISSUER='https://evil.test',
               SSL_CERT_FILE='bad.pem', QTWEBENGINE_CHROMIUM_FLAGS='--ignore-certificate-errors',
               PYTHONPATH='wrong', HTTPS_PROXY='https://evil.test')
    command = functions + "\n$program='E:\\Codex'; $runtimeTemp='E:\\Codex\\Temp'; (New-CleanProcess 'test.exe').EnvironmentVariables.Keys | ConvertTo-Json"
    result = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-Command', command],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    keys = {key.upper() for key in json.loads(result.stdout)}
    assert not keys & {'MENTORFLOW_DESKTOP_WORKSPACE_ID', 'MENTORFLOW_DESKTOP_OIDC_ISSUER',
                       'SSL_CERT_FILE', 'QTWEBENGINE_CHROMIUM_FLAGS', 'PYTHONPATH', 'HTTPS_PROXY'}
    assert 'MENTORFLOW_DESKTOP_ORGANIZATION_ID =' not in source
    assert 'MENTORFLOW_DESKTOP_WORKSPACE_ID =' not in source
    assert '--no-data-check --no-smoke' in source


def test_preparation_refuses_overwrite(tmp_path):
    source = fake_package(tmp_path / 'old')
    destination = tmp_path / 'existing'
    destination.mkdir()
    with pytest.raises(ValueError, match='overwrite'):
        preparation.prepare(source, destination)


def test_launcher_ignores_inherited_powershell_module_path(tmp_path):
    source = TEMPLATE.read_text(encoding='utf-8')
    initialization = source[:source.index('# Release-time trust anchors')]
    result = subprocess.run([
        shutil.which('powershell.exe'), '-NoProfile', '-NonInteractive', '-Command',
        initialization + "\n(Get-Command Get-FileHash).Module.Name; $env:PSModulePath -eq (Join-Path $PSHOME 'Modules')",
    ], env=dict(os.environ, PSModulePath=str(tmp_path / 'untrusted-modules')),
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ['Microsoft.PowerShell.Utility', 'True']
