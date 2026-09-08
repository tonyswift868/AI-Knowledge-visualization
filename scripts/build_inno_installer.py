"""Build the Inno 7 candidate; never install, elevate, publish or update anything.

Production input is the existing verified Windows package. --fixture creates a
small isolated UX payload; its installer cannot write outside E:/Codex/Temp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('E:/Codex/App/InnoSetup-7.1.0/ISCC.exe')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_core_payload(payload, release_path):
    from scripts.per_user_install import keys, verify_tree
    from desktop.update_protocol import verify_release
    from scripts.install_product import configured
    if not release_path:
        raise ValueError('Signed per-user BASE release is required; old flat payload is not a candidate')
    release = verify_release(Path(release_path).read_bytes(), keys(), current='0.0.0')
    verify_tree(payload, release)
    if not (payload / 'UserRuntime/UserRuntime.exe').is_file():
        raise ValueError('Signed stable UserRuntime is missing')
    return configured().app_id


def build(args):
    output = Path(args.output).resolve()
    payload = output / 'build-input/payload' if args.fixture else Path(args.payload).resolve(strict=True)
    if not args.fixture and (output == payload or payload in output.parents):
        raise ValueError('Output must be outside the payload')
    output.mkdir(parents=True, exist_ok=True)
    stage = output / 'build-input'
    stage.mkdir(exist_ok=True)
    if args.fixture:
        payload = stage / 'payload'
        payload.mkdir(exist_ok=True)
        # An inert PE, never the product runtime. Finish can launch it safely.
        subprocess.run(['powershell', '-NoProfile', '-Command',
            "Add-Type -TypeDefinition 'public class UX { public static void Main() {} }' "
            "-OutputAssembly $env:MF_INNO_FIXTURE_EXE -OutputType WindowsApplication"],
            env={**os.environ, 'MF_INNO_FIXTURE_EXE': str(payload / 'MentorFlow.exe')},
            check=True) if not (payload / 'MentorFlow.exe').exists() else None
        (payload / 'welcome.txt').write_text('MentorFlow installer UX payload\n', encoding='utf-8')
        (payload / 'sample.dat').write_bytes(bytes(16 * 1024 * 1024))
    else:
        payload = Path(args.payload).resolve(strict=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts/verify_windows_package.py'),
                        'verify', '--package-root', str(payload), '--no-smoke',
                        '--no-data-check'], check=True)
    app_id = ('MentorFlow.Inno.Fixture' if args.fixture else
              verify_core_payload(payload, getattr(args, 'release', None)))
    if not (payload / 'MentorFlow.exe').is_file():
        raise ValueError('Payload must contain MentorFlow.exe')
    if output == payload or payload in output.parents:
        raise ValueError('Output must be outside the payload')
    files = []
    directories = []
    for path in sorted(payload.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f'Payload links are forbidden: {path}')
        if path.is_dir():
            directories.append(str(path.relative_to(payload)))
        if path.is_file():
            rel = str(path.relative_to(payload))
            if any(c in rel for c in '\n\r";='):
                raise ValueError('Unsupported payload filename')
            files.append((rel, path.stat().st_size))
    version_root = ROOT if args.fixture else payload
    version = json.loads((version_root / 'mentorflow-version.json').read_text(encoding='utf-8-sig'))['version']
    ownership = stage / 'ownership.ini'
    exe_hash = digest(payload / 'MentorFlow.exe')
    ownership.write_text('[Product]\nId=MentorFlow.Inno.v1\nExecutableSHA256=' + exe_hash +
                         '\n[Files]\n' + '\n'.join(f'{rel}=1' for rel, _ in files) +
                         '\n[Directories]\n' + '\n'.join(f'{rel}=1' for rel in directories) + '\n',
                         encoding='utf-16')
    (stage / 'shell.ini').write_text('[Shell]\nAppId=' + app_id +
        '\nMode=per_user\nEngine=UserRuntime\nLauncher=Updater\\UserRuntime.exe launch\n', encoding='utf-16')
    from PIL import Image
    source = ROOT / 'frontend/public/teacher2d/tsubaki-live2d/icon.png'
    # Same canonical source as scripts/build_desktop_icon.py, without repainting.
    with Image.open(source) as icon:
        icon.convert('RGBA').save(stage / 'MentorFlow.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])
    shutil.copyfile(source, stage / 'MentorFlow.png')
    stem = 'MentorFlow-UX-Candidate' if args.fixture else 'MentorFlow-Setup'
    options = {
        'PayloadDir': payload, 'OutputDir': output, 'OutputName': stem,
        'BuildInput': stage, 'ProductVersion': version,
        'RequiredBytes': sum(size for _, size in files) + 256 * 1024 * 1024,
        'FixtureBuild': int(args.fixture), 'Appearance': args.appearance,
        'ProductAppId': app_id, 'ShortcutName': 'MentorFlow 预览版' if '.UAT.' in app_id else 'MentorFlow',
        'SignedRelease': '' if args.fixture else Path(args.release).absolute(),
    }
    command = [args.compiler] + [f'/D{k}={v}' for k,v in options.items()]
    command.append(str(ROOT / 'installer/inno/MentorFlow.iss'))
    source_paths = [ROOT / 'scripts/build_inno_installer.py', source,
                    *sorted((ROOT / 'installer/inno').glob('*.iss'))]
    source_hashes = {str(path.relative_to(ROOT)): digest(path) for path in source_paths}
    manifest = payload / 'SELF-CHECK/package-manifest.json'
    manifest_hash = None if args.fixture else digest(manifest)
    release_hash = None if args.fixture else digest(args.release)
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    (output / 'compile.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        print(result.stdout + result.stderr)
        raise RuntimeError(f'ISCC failed: {result.returncode}')
    if any(digest(ROOT / path) != value for path, value in source_hashes.items()):
        raise RuntimeError('Installer source changed during compilation; candidate is not frozen')
    if not args.fixture and digest(manifest) != manifest_hash:
        raise RuntimeError('Payload manifest changed during compilation')
    if not args.fixture:
        verify_core_payload(payload, args.release)
        if digest(args.release) != release_hash:
            raise RuntimeError('Signed release changed during compilation')
    artifact = output / (stem + '.exe')
    receipt = dict(artifact=str(artifact), sha256=digest(artifact),
                   payload_bytes=sum(size for _,size in files), file_count=len(files),
                   fixture=args.fixture, compiler=args.compiler, command=command,
                   source_sha256=source_hashes, package_manifest_sha256=manifest_hash)
    receipt.update(app_id=app_id, signed_release_sha256=release_hash,
                   install_mode='inert_fixture' if args.fixture else 'per_user',
                   stable_launcher=None if args.fixture else 'Updater/UserRuntime.exe launch --root <selected-root>',
                   app_tree_owned_by='fixture_only' if args.fixture else 'per_user_install', installer_executed=False)
    (output / 'build-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt, indent=2))
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', default=str(ROOT / 'release/distributable/MentorFlow'))
    parser.add_argument('--release', help='Signed BASE release matching the compiled public authority and complete payload')
    parser.add_argument('--output', default='E:/Codex/Temp/mf-modern-installer-20260908/candidate')
    parser.add_argument('--compiler', default=str(COMPILER))
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--appearance', choices=['dynamic','light','dark'], default='dynamic')
    args = parser.parse_args()
    if not args.fixture and args.appearance != 'dynamic':
        parser.error('Production follows Windows; forced appearances are UX fixtures only')
    build(args)


if __name__ == '__main__':
    main()
