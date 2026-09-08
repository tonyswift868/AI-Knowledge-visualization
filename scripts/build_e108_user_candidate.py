"""Assemble one E108 Inno user candidate from a clean final Git source.

No installer/App/UAC execution, production deployment, push or dependency
installation. Reuses only verified dependency files and frozen dependency TOCs;
recompiles the App and stable runtime from the current source.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP_ID = 'MentorFlow.UAT.E108Assembly1'
KEY_ID = 'e108-user-test-only'
BASE_VERSION, TARGET_VERSION = '1.4.0', '1.4.1'
ORIGIN = 'http://127.0.0.1:48218'


def sha(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')


def run(command, log, env):
    with Path(log).open('xb') as stream:
        result = subprocess.run([str(item) for item in command], cwd=ROOT, env=env,
            stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=1800)
    if result.returncode: raise RuntimeError('Failed command; preserved log: ' + str(log))


def identity():
    def git(*args): return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
    if git('status', '--porcelain=v1', '--untracked-files=all'):
        raise ValueError('Final source must be committed and clean before assembly')
    return dict(source_sha=git('rev-parse', 'HEAD'), tree_sha=git('rev-parse', 'HEAD^{tree}'), app_id=APP_ID)


def initialize(args):
    source = identity()
    from scripts.prepare_transactional_installer import verify
    verify(args.seed_payload, args.seed_manifest_sha256)
    for path in (args.seed_pyz_toc, args.seed_exe_toc):
        if not path.is_file(): raise ValueError('Frozen dependency TOC is missing')
    # Never overwrite an earlier full package or incomplete build.
    args.work.mkdir(parents=False, exist_ok=False)
    args.output.mkdir(parents=False, exist_ok=False)
    generated = args.work / 'generated'; generated.mkdir()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    private = Ed25519PrivateKey.generate()
    key = args.work / 'test-signing-key.pem'
    key.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    authority = (f'APP_ID={APP_ID!r}\nPUBLIC_KEYS={{{KEY_ID!r}:{public!r}}}\n'
        'import os\nfrom pathlib import Path\n'
        f'WORK_ROOT=str(Path(os.environ["LOCALAPPDATA"])/{APP_ID!r})\n'
        'DATA_ROOT=str(Path(WORK_ROOT)/"data")\n'
        f'SOURCE_SHA={source["source_sha"]!r}\n')
    (generated / 'mentorflow_delivery_authority.py').write_text(authority, encoding='utf-8')
    emit(args.work / 'build-input.json', dict(source=source, seed_manifest_sha256=args.seed_manifest_sha256,
        seed_pyz_toc_sha256=sha(args.seed_pyz_toc), seed_exe_toc_sha256=sha(args.seed_exe_toc),
        public_key=base64.b64encode(public).decode(), key_id=KEY_ID))
    env = {**os.environ, 'PYTHONPATH': str(generated) + os.pathsep + str(ROOT),
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONIOENCODING': 'utf-8',
           'TEMP': str(args.work), 'TMP': str(args.work),
           'PYINSTALLER_CONFIG_DIR': 'E:/Codex/Data/cache/pyinstaller'}
    # Fresh interpreter: the compiled UAT namespace must precede all protocol imports.
    run([sys.executable, '-B', '-m', 'scripts.build_e108_user_candidate', *sys.argv[1:], '--execute'],
        args.work / 'assembly.log', env)


def verify_binaries(app, runtime):
    import marshal
    import types
    import pefile
    from PyInstaller.archive.readers import CArchiveReader
    def normalize(code):
        if isinstance(code, types.CodeType):
            return code.replace(co_filename='<source>', co_consts=tuple(normalize(c) for c in code.co_consts))
        return code
    common = ['scripts.per_user_install', 'scripts.per_user_update_core', 'scripts.per_user_launcher',
              'scripts.per_user_runtime_session', 'desktop.update_session', 'desktop.update_protocol',
              'desktop.update_service', 'desktop.update_download']
    checks = []
    for path, entry, filename, extra in (
        (app, 'desktop_gui_entry', 'desktop_gui_entry.py', ['desktop.update_panel', 'desktop.update_controller',
            'desktop.update_lifecycle', 'desktop.gui_update_runtime', 'desktop.system_page']),
        (runtime, 'per_user_install', 'scripts/per_user_install.py', ['scripts.serve_signed_test_feed'])):
        reader = CArchiveReader(str(path)); pyz = reader.open_embedded_archive('PYZ.pyz')
        if {'scripts.measure_installed_login_gate', 'scripts.test_login_form_probe'} & set(pyz.toc):
            raise ValueError('Unrelated observation module entered the candidate')
        for name in common + extra:
            source = ROOT / (name.replace('.', '/') + '.py')
            if normalize(pyz.extract(name)) != normalize(compile(source.read_bytes(), str(source), 'exec')):
                raise ValueError('Packaged module is not current source: ' + name)
        if normalize(marshal.loads(reader.extract(entry))) != normalize(compile((ROOT / filename).read_bytes(), filename, 'exec')):
            raise ValueError('Packaged entrypoint is not current source')
        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])
            manifests = [pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size).decode('utf-8')
                for resource in pe.DIRECTORY_ENTRY_RESOURCE.entries if resource.id == 24
                for item in resource.directory.entries for lang in item.directory.entries]
            if not manifests or any('asInvoker' not in text or 'requireAdministrator' in text for text in manifests):
                raise ValueError('Consumer runtime execution level is not asInvoker')
        finally: pe.close()
        checks.append(dict(path=str(path), sha256=sha(path), modules=common + extra, as_invoker=True))
    return checks


def execute(args):
    from desktop.update_protocol import canonical
    from scripts.prepare_transactional_installer import verify
    from scripts.prepare_gui_bootstrap_closure import collect_closure
    from scripts import build_release as publisher
    from mentorflow_delivery_authority import PUBLIC_KEYS
    inputs = json.loads((args.work / 'build-input.json').read_bytes())
    source = identity()
    if source != inputs['source']: raise ValueError('Source changed after build admission')
    for path, name in ((args.seed_pyz_toc, 'seed_pyz_toc_sha256'), (args.seed_exe_toc, 'seed_exe_toc_sha256')):
        if sha(path) != inputs[name]: raise ValueError('Frozen dependency closure changed')
    generated = args.work / 'generated'
    env = {**os.environ, 'MF_DELIVERY_PROJECT': str(ROOT), 'MF_DELIVERY_BUILD': str(generated),
           'MF_DELIVERY_SEED_PYZ_TOC': str(args.seed_pyz_toc), 'MF_DELIVERY_SEED_EXE_TOC': str(args.seed_exe_toc)}
    env.pop('MF_DELIVERY_ANALYSIS', None)
    run([sys.executable, '-B', '-m', 'scripts.build_desktop_icon'], args.work / 'icon.log', env)
    command = [sys.executable, '-B', '-m', 'PyInstaller', '--onedir', '--windowed', '--name', 'UserRuntime',
        '--paths', ROOT, '--paths', generated, '--hidden-import', 'mentorflow_delivery_authority',
        '--distpath', args.work / 'native', '--workpath', args.work / 'pyi-runtime',
        '--specpath', generated, '--noupx']
    for module in ('torch', 'PySide6', 'numpy', 'pandas', 'matplotlib', 'scipy', 'transformers', 'docling', 'pytest', 'backend', 'desktop.main'):
        command += ['--exclude-module', module]
    run(command + [ROOT / 'scripts/per_user_install.py'], args.work / 'runtime.log', env)
    run([sys.executable, '-B', '-m', 'PyInstaller', '--distpath', args.work / 'app-exe',
         '--workpath', args.work / 'pyi-app', ROOT / 'release/real-delivery-app.spec'], args.work / 'app.log', env)
    exe = args.work / 'pyi-app/real-delivery-app/MentorFlow.exe'
    native = args.work / 'native/UserRuntime'
    binary_checks = verify_binaries(exe, native / 'UserRuntime.exe')
    _, rows = verify(args.seed_payload, args.seed_manifest_sha256)
    base = args.work / 'BASE'; base.mkdir()
    replaced = {'MentorFlow.exe', 'mentorflow-version.json', '_internal/mentorflow-version.json',
                'SELF-CHECK/source-identity.json', 'SELF-CHECK/gui-bootstrap-closure.json', 'SELF-CHECK/package-manifest.json'}
    for name, row in rows.items():
        if name in replaced or name.startswith(('UserRuntime/', 'Installer/')): continue
        output = base / name; output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.seed_payload / name, output)
        if sha(output) != row['sha256']: raise ValueError('Verified dependency copy mismatch')
    shutil.copyfile(exe, base / 'MentorFlow.exe')
    shutil.copytree(native, base / 'UserRuntime')
    (base / 'SELF-CHECK').mkdir(exist_ok=True)
    (base / 'SELF-CHECK/source-identity.json').write_bytes(canonical(source))
    def version_doc(version):
        return dict(schema='mentorflow.version.v1', product='MentorFlow', version=version, channel='stable',
            desktop_notice='E108 用户测试候选，当前用户安装。',
            update=dict(protocol='mentorflow.signed-file-release.v1', enabled=True, metadata_url=ORIGIN + '/stable.json',
                        public_keys={key: base64.b64encode(value).decode() for key, value in PUBLIC_KEYS.items()}))
    version_paths = ('mentorflow-version.json', '_internal/mentorflow-version.json')
    for name in version_paths: (base / name).write_bytes(canonical(version_doc(BASE_VERSION)))
    (base / 'SELF-CHECK/gui-bootstrap-closure.json').write_bytes(canonical(collect_closure(base)))
    manifest, _ = publisher.inventory(base, BASE_VERSION)
    (base / 'SELF-CHECK/package-manifest.json').write_bytes(canonical(manifest))
    target = args.work / 'TARGET'; target.mkdir()
    # The Inno payload gate requires independent regular files. Even a private
    # TARGET hardlink would make the BASE input fail that gate via st_nlink.
    for path in base.rglob('*'):
        name = path.relative_to(base)
        if path.is_file() and name.as_posix() not in (*version_paths, 'SELF-CHECK/package-manifest.json'):
            dest = target / name; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, dest)
    for name in version_paths: (target / name).write_bytes(canonical(version_doc(TARGET_VERSION)))
    manifest, _ = publisher.inventory(target, TARGET_VERSION)
    (target / 'SELF-CHECK/package-manifest.json').write_bytes(canonical(manifest))
    notes = args.work / 'release-notes.txt'
    notes.write_text('E108 安装、应用内增量下载和重启验证；不更改用户学习资料。', encoding='utf-8')
    feed = args.output / 'Updates'
    receipts = []
    for package, version, prior in ((base, BASE_VERSION, None), (target, TARGET_VERSION, base)):
        opts = publisher.parser().parse_args(['--target', str(package), '--output', str(feed), '--version', version,
            '--channel', 'stable', '--notes', str(notes), '--origin', ORIGIN, '--source-sha', source['source_sha'],
            '--tree-sha', source['tree_sha'], '--signing-key', str(args.work / 'test-signing-key.pem'), '--key-id', KEY_ID,
            '--uat-loopback', '--min-supported-version', BASE_VERSION, '--expiry-days', '31'] +
            (['--base', str(prior)] if prior else []))
        receipts.append(publisher.build(opts))
    base_release = feed / BASE_VERSION / 'stable/release.json'
    run([sys.executable, '-B', '-m', 'scripts.build_inno_installer', '--payload', base,
         '--release', base_release, '--output', args.output / 'Installer'], args.work / 'installer.log', env)
    shutil.copytree(native, args.output / 'TestSource/Runtime')
    (args.output / 'Start-Test-Update-Source.cmd').write_bytes((
        '@echo off\r\nstart "" /b "%~dp0TestSource\\Runtime\\UserRuntime.exe" serve-test-source '
        '--feed "%~dp0Updates" --port 48218 --seconds 7200\r\n').encode('ascii'))
    emit(args.work / 'assembled-artifacts.json', dict(source=source, binary_checks=binary_checks,
        app=str(base), target=str(target), releases=receipts, output=str(args.output),
        status='BUILT_PENDING_PACKAGED_CONSUMER_CHECK', private_key_packaged=False,
        app_executions=0, installer_executions=0, uac_executions=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seed-payload', type=Path, required=True)
    parser.add_argument('--seed-manifest-sha256', required=True)
    parser.add_argument('--seed-pyz-toc', type=Path, required=True)
    parser.add_argument('--seed-exe-toc', type=Path, required=True)
    parser.add_argument('--execute', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    for name in ('work', 'output', 'seed_payload', 'seed_pyz_toc', 'seed_exe_toc'):
        setattr(args, name, getattr(args, name).absolute())
    if args.execute: execute(args)
    else: initialize(args)


if __name__ == '__main__': main()
