"""One Inno Setup 7 product shell. Never launch, install or publish anything.

--check-source uses the real compiler with output disabled: no installer EXE.
Production requires A's explicit final payload and a separate empty output folder.
Only MentorFlow-Setup.exe goes there; compiler inputs/evidence stay in --work-dir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
COMPILER = Path('E:/Codex/App/InnoSetup-7.1.0/ISCC.exe')
OUTPUT_NAME = 'MentorFlow-Setup.exe'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def collect_payload(payload):
    files, directories = [], []
    for path in sorted(payload.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f'Payload links are forbidden: {path}')
        rel = str(path.relative_to(payload))
        if any(c in rel for c in '\n\r";=[]'):
            raise ValueError('Unsupported payload filename')
        if path.is_dir():
            directories.append(rel)
        elif path.is_file():
            if path.parts[len(payload.parts)].lower() == 'installer' or re.fullmatch(
                    r'(?i)mentorflow.*(?:setup|candidate).*\.exe', path.name):
                raise ValueError('Payload must not contain installer candidates')
            if path.name.lower() == '.mentorflow-install.ini':
                raise ValueError('Input must not contain a previous shell ownership record')
            files.append((rel, path.stat().st_size))
    return files, directories


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
    check_source = args.check_source
    work = Path(args.work_dir).resolve()
    output = None if check_source else Path(args.output).resolve()
    payload = None if check_source else Path(args.payload).resolve(strict=True)
    if not check_source:
        if overlaps(output, payload):
            raise ValueError('Output must be outside and disjoint from the payload')
        if overlaps(work, payload) or overlaps(work, output):
            raise ValueError('Work directory must be disjoint from payload and output')
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise ValueError('User output must be empty; do not overwrite or retain alternate candidates')
        subprocess.run([sys.executable, str(ROOT / 'scripts/verify_windows_package.py'),
                        'verify', '--package-root', str(payload), '--no-smoke',
                        '--no-data-check'], check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if not (payload / 'MentorFlow.exe').is_file():
            raise ValueError('Payload must contain MentorFlow.exe')

        app_id = verify_core_payload(payload, getattr(args, 'release', None))

    work.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='inno-source-' if check_source else 'inno-product-', dir=work))
    if check_source:
        # Non-PE compiler input only. --output=no cannot produce a runnable shell.
        payload = stage / 'input'
        payload.mkdir()
        (payload / 'MentorFlow.exe').write_bytes(b'INNO SOURCE CHECK INPUT; NOT A PE')
        version = '0.0.0'
        app_id = 'MentorFlow.Inno.SourceCheck'
        (payload / 'UserRuntime').mkdir()
        (payload / 'UserRuntime/UserRuntime.exe').write_bytes(b'INERT COMPILER INPUT')
        release_path = stage / 'base-release.json'
        release_path.write_bytes(b'INERT COMPILER INPUT')
    else:
        version = json.loads((payload / 'mentorflow-version.json').read_text(encoding='utf-8-sig'))['version']
        if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?(?:\+[A-Za-z0-9.-]+)?', version):
            raise ValueError('Invalid product version')
        release_path = Path(args.release).resolve(strict=True)
    files, directories = collect_payload(payload)
    exe_hash = digest(payload / 'MentorFlow.exe')
    (stage / 'ownership.ini').write_text(
        '[Product]\nId=MentorFlow.Inno.v1\nVersion=' + version +
        '\nExecutableSHA256=' + exe_hash +
        '\n[Files]\n' + '\n'.join(f'{rel}=1' for rel, _ in files) +
        '\n[Directories]\n' + '\n'.join(f'{rel}=1' for rel in directories) + '\n',
        encoding='utf-16')
    (stage / 'shell.ini').write_text('[Shell]\nAppId=' + app_id +
        '\nMode=per_user\nEngine=UserRuntime\nLauncher=Updater\\UserRuntime.exe launch\n', encoding='utf-16')
    from PIL import Image
    source = ROOT / 'frontend/public/teacher2d/tsubaki-live2d/icon.png'
    with Image.open(source) as icon:
        icon.convert('RGBA').save(stage / 'MentorFlow.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])
    shutil.copyfile(source, stage / 'MentorFlow.png')
    required = sum(size for _, size in files) + 256 * 1024 * 1024
    if check_source:
        required = args.check_required_bytes
        if required <= 0:
            raise ValueError('Required bytes must be positive')
    options = {
        'PayloadDir': payload, 'OutputDir': stage, 'BuildInput': stage,
        'ProductVersion': version, 'PayloadExeSHA256': exe_hash, 'RequiredBytes': required,
        'ProductAppId': app_id, 'ShortcutName': 'MentorFlow 预览版' if '.UAT.' in app_id else 'MentorFlow',
        'SignedRelease': release_path,
    }
    command = [args.compiler, '--no-ide-signtools']
    if check_source:
        command.append('--output=no')
    command += [f'/D{k}={v}' for k, v in options.items()]
    command.append(str(ROOT / 'installer/inno/MentorFlow.iss'))
    source_paths = [ROOT / 'scripts/build_inno_installer.py', source,
                    *sorted((ROOT / 'installer/inno').glob('*.iss'))]
    source_hashes = {str(path.relative_to(ROOT)): digest(path) for path in source_paths}
    manifest = payload / 'SELF-CHECK/package-manifest.json'
    manifest_hash = None if check_source else digest(manifest)
    release_hash = None if check_source else digest(release_path)
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    (stage / 'compile.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        print(result.stdout + result.stderr)
        raise RuntimeError(f'ISCC failed: {result.returncode}')
    if any(digest(ROOT / path) != value for path, value in source_hashes.items()):
        raise RuntimeError('Installer source changed during compilation; candidate is not frozen')
    if not check_source and digest(manifest) != manifest_hash:
        raise RuntimeError('Payload manifest changed during compilation')

    if not check_source:
        if digest(release_path) != release_hash:
            raise RuntimeError('Signed release changed during compilation')
        verify_core_payload(payload, release_path)

    receipt = dict(check_source=check_source, artifact=None, sha256=None,
                   payload_bytes=sum(size for _,size in files), file_count=len(files),
                   required_bytes=required, compiler=args.compiler, command=command,
                   source_sha256=source_hashes, package_manifest_sha256=manifest_hash,
                   installer_launches=0, installer_exe_emitted=False, evidence_dir=str(stage),
                   app_id=app_id, signed_release_sha256=release_hash, install_mode='per_user',
                   stable_launcher='Updater/UserRuntime.exe launch --root <selected-root>',
                   app_tree_owned_by='per_user_install')
    if check_source:
        if (stage / OUTPUT_NAME).exists():
            raise RuntimeError('Source check unexpectedly produced installer output')
    else:
        private_artifact = stage / OUTPUT_NAME
        private_hash = digest(private_artifact)
        output.mkdir(parents=True, exist_ok=True)
        if any(output.iterdir()):
            raise RuntimeError('Output changed during compilation; refusing delivery')
        artifact = output / OUTPUT_NAME
        # Exclusive creation prevents overwriting a concurrently delivered installer.
        with artifact.open('xb') as target, private_artifact.open('rb') as src:
            shutil.copyfileobj(src, target, length=1024*1024)
        if digest(artifact) != private_hash:
            raise RuntimeError('Copied artifact hash mismatch; do not publish')
        receipt.update(artifact=str(artifact), sha256=private_hash, installer_exe_emitted=True)
    (stage / 'build-receipt.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt, indent=2))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--payload', help="A's explicitly selected final verified product payload")
    mode.add_argument('--check-source', action='store_true', help='Compile only, no installer output')
    parser.add_argument('--release', help='Signed BASE envelope matching the compiled authority and complete payload')
    parser.add_argument('--output', help='Empty user-facing folder; receives exactly one EXE')
    parser.add_argument('--work-dir', required=True, help='Private build inputs/evidence; separate from output')
    parser.add_argument('--compiler', default=str(COMPILER))
    parser.add_argument('--check-required-bytes', type=int, default=2468435456,
                        help='Compile-only arithmetic boundary; no real payload/space claim')
    args = parser.parse_args()
    if args.check_source and args.output:
        parser.error('--check-source cannot publish to a user output directory')
    if not args.check_source and not args.output:
        parser.error('Production requires --output; no default payload or output is assumed')
    build(args)


if __name__ == '__main__':
    main()
