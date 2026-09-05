"""Layout-only release preparation. Reuse frozen binaries; never rebuild them."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from scripts import verify_windows_package as verifier

ROOT = Path(__file__).resolve().parents[2]
BASELINE_EXE_SHA256 = '9684ca57c7da5e91f4b91433e1ffe6200234121de97102538c6d556d518e5ef8'


def prepare(source: Path, destination: Path) -> dict:
    source = verifier._validate_package_root(source).resolve()
    destination = destination.absolute()
    if destination.exists():
        raise ValueError('Refusing to overwrite an existing release')
    verifier._ensure_e_drive(destination, label='release output')
    verifier._validate_existing_parent_chain(destination, label='release output')
    if verifier.sha256_file(source / 'MentorFlow.exe') != BASELINE_EXE_SHA256:
        raise ValueError('Input EXE is not the frozen authorized baseline')
    # Check input links before copy; preserve the original delivery verbatim.
    for _ in verifier._iter_package_entries(source):
        pass
    program = destination / 'MentorFlow'
    program.mkdir(parents=True)
    for entry in source.iterdir():
        if entry.name == 'account-core-launch.json':
            continue  # Legacy mutable sidecar stays intact in the old release.
        if entry.is_dir():
            shutil.copytree(entry, program / entry.name)
        else:
            shutil.copy2(entry, program / entry.name)
    self_check = program / 'SELF-CHECK'
    self_check.mkdir(exist_ok=True)
    shutil.copy2(program / 'MentorFlowSelfCheck.exe', self_check / 'MentorFlowSelfCheck.exe')
    for name in ('verify_windows_package.py', 'mentorflow-check.bat', 'mentorflow-update.bat'):
        shutil.copy2(ROOT / 'scripts' / name, self_check / name)
    # Standard immutable payload manifest, including all EXE/DLL files.
    manifest = verifier.build_manifest(program)
    config_dir = destination / 'Runtime'
    config_dir.mkdir()
    (config_dir / 'launch.json').write_text(json.dumps({
        'schema': 'mentorflow.desktop-runtime.v1', 'data_profile': 'default',
    }, indent=2) + '\n', encoding='utf-8')
    certificate_dir = destination / 'Config'
    certificate_dir.mkdir()
    shutil.copy2(source.parent / 'Config' / 'edge-tls-ca.crt', certificate_dir / 'edge-tls-ca.crt')
    launcher = (ROOT / 'scripts/enterprise/start_account_core_package.ps1').read_text(encoding='utf-8')
    launcher = launcher.replace('__MANIFEST_SHA256__', verifier.sha256_file(manifest).upper())
    launcher = launcher.replace('__VERIFIER_SHA256__', verifier.sha256_file(self_check / 'MentorFlowSelfCheck.exe').upper())
    (destination / 'Start-MentorFlow.ps1').write_text(launcher, encoding='utf-8')
    (destination / 'Start-MentorFlow.cmd').write_text(
        '@echo off\n"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        '-NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-MentorFlow.ps1"\n'
        'if errorlevel 1 pause\n', encoding='ascii')
    return {
        'rebuild': False, 'source_exe_sha256': BASELINE_EXE_SHA256,
        'exe_sha256': verifier.sha256_file(program / 'MentorFlow.exe'),
        'manifest_sha256': verifier.sha256_file(manifest),
        'launcher_sha256': verifier.sha256_file(destination / 'Start-MentorFlow.ps1'),
        'program': str(program), 'runtime': str(config_dir / 'launch.json'),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(prepare(options.source, options.destination), indent=2))
