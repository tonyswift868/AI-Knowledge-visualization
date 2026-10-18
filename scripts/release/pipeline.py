"""Workflow glue. --local-server-root runs publication without SSH or network."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_learning_installer as builder
from scripts.release import server_ops
from scripts.release.transport import LocalServer, SSHServer, fetched
from scripts.server_installer.assemble_payload import assemble_payload, verified_release
from refactor.desktop.delivery.update_protocol import version_key, make_login_policy

PUBLIC = Path(__file__).parent / 'inputs/public-key.json'
ORIGIN = 'https://89.45.45.28/mentorflow/updates/app/objects/'
INNO_URL = 'https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe'
INNO_SHA256 = '0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f'


def rows_of(release):
    return list({r['sha256']: r for r in release.rows.values()}.values())


def require_version(release, requested):
    server_ops.version(requested)
    if release.version != requested:
        raise ValueError('requested version differs from signed manifest')


def download_objects(server, release, destination):
    destination.mkdir(parents=True, exist_ok=True)
    def get(row):
        target = destination / row['sha256']
        if not target.exists():
            temp = target.with_suffix('.part')
            try:
                if isinstance(server, LocalServer):
                    source = server.app / 'objects' / target.name
                    server_ops.checked(source, row)
                    server_ops.link_or_copy(source, temp)
                else:
                    # Self-signed transport is accepted ONLY at this fixed public origin.
                    # Authenticity is the provisioned key + existing signed release verifier.
                    with urlopen(ORIGIN + target.name, timeout=60,
                                 context=ssl._create_unverified_context()) as response, temp.open('wb') as out:
                        shutil.copyfileobj(response, out)
                server_ops.checked(temp, row)
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
        server_ops.checked(target, row)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(get,row) for row in rows_of(release)]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def installer(server, version, work, compiler, *, public_key=PUBLIC, latest=False, compile_fn=None):
    work.mkdir(parents=True, exist_ok=True)
    stable = fetched(server, version, work / 'stable.json')
    release = verified_release(stable, public_key)
    require_version(release, version)
    objects, payload = work / 'objects', work / 'payload'
    download_objects(server, release, objects)
    stats = assemble_payload(stable, objects, payload, public_key=public_key)
    (compile_fn or builder.installer)(argparse.Namespace(payload=payload, release=stable,
        work=work / 'inno', output=work / 'installer', compiler=Path(compiler)))
    exe = work / 'installer/MentorFlow-Setup.exe'
    result = server.call('installer', version, transfer=server.upload(exe),
        installer=dict(size=exe.stat().st_size, sha256=builder.digest(exe)),
        manifest=base64.b64encode(stable.read_bytes()).decode(), latest=latest)
    return dict(assembly=stats, publish=result, installer_bytes=exe.stat().st_size,
                installer_sha256=builder.digest(exe))


def stage_objects(server, unsigned, objects, work):
    body = json.loads(Path(unsigned).read_bytes())
    version = server_ops.version(body['version'])
    # Build output is intentionally unsigned here. Never treat it as a published release.
    rows = list({r['sha256']: r for r in body['full_release']['assets']}.values())
    missing = set(server.call('missing', version, rows=rows)['missing'])
    selected = [r for r in rows if r['sha256'] in missing]
    for row in selected:
        server_ops.checked(Path(objects) / row['sha256'], row)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / 'missing-objects.tar'
    try:
        with tarfile.open(archive, 'w') as tar:
            for row in selected:
                tar.add(Path(objects) / row['sha256'], arcname=row['sha256'], recursive=False)
        result = server.call('stage_objects', version, rows=selected, transfer=server.upload(archive))
        return dict(**result, bytes=sum(r['size'] for r in selected), existing=len(rows)-len(selected))
    finally:
        archive.unlink(missing_ok=True)


def promote(server, version, work, *, public_key=PUBLIC):
    work.mkdir(parents=True, exist_ok=True)
    stable = fetched(server, version, work / 'staged-stable.json', staged=True)
    release = verified_release(stable, public_key)
    require_version(release, version)
    live = fetched(server, version, work / 'old-stable.json', live=True)
    prior = verified_release(live, public_key)
    if stable.read_bytes() != live.read_bytes():
        if version_key(release.version) <= version_key(prior.version):
            raise ValueError('refuse replacing published version with same or older version')
        if release.signed['metadata_version'] <= prior.signed['metadata_version']:
            raise ValueError('metadata_version must increase')
        if version_key(release.signed['min_supported_version']) < version_key(prior.signed['min_supported_version']):
            raise ValueError('mandatory floor cannot be lowered')
    return server.call('promote', version, manifest=base64.b64encode(stable.read_bytes()).decode(),
                       rows=rows_of(release), login_policy=make_login_policy(release))


def stage_manifest(server, stable, public_key):
    release = verified_release(stable, public_key)
    return server.call('stage_manifest', release.version, transfer=server.upload(stable),
        sha256=builder.digest(stable), manifest=base64.b64encode(Path(stable).read_bytes()).decode(),
        login_policy=make_login_policy(release))


def provision_inno(work):
    """CI only: local operator must pass their existing --compiler, never install."""
    if not os.environ.get('GITHUB_ACTIONS') == 'true':
        raise ValueError('Inno provisioning is GitHub-runner-only; use existing --compiler locally')
    work.mkdir(parents=True, exist_ok=True)
    setup = work / 'innosetup-7.1.0-x64.exe'
    with urlopen(INNO_URL, timeout=90) as response, setup.open('wb') as out:
        shutil.copyfileobj(response, out)
    if builder.digest(setup) != INNO_SHA256:
        raise ValueError('official Inno 7.1.0 installer SHA256 mismatch')
    destination = work / 'inno-7.1.0'
    subprocess.run([str(setup), '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                    '/DIR=' + str(destination)], check=True)
    compiler = destination / 'ISCC.exe'
    if not compiler.is_file():
        raise FileNotFoundError(compiler)
    if not (destination / 'Languages/ChineseSimplified.isl').is_file():
        raise FileNotFoundError('official Inno package missing ChineseSimplified.isl')
    return compiler


def build(server, args):
    """Use the migrated build controller; retain objects only until optional staging."""
    from scripts.release import build_release as controller
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    previous = fetched(server, args.version, work / 'previous-stable.json', live=True)
    # Verify before invoking freeze, and take a fixed signed snapshot for release.
    verified_release(previous, args.public_key)
    command = [sys.executable, '-B', str(Path(controller.__file__)), '--version', args.version,
               '--baseline', args.baseline or os.environ.get('GITHUB_SHA') or controller.git(ROOT, 'rev-parse', 'HEAD'),
               '--notes', args.notes, '--valid-days', '1096', '--base-versions', *args.base_versions.split(),
               '--output-dir', str(work / 'build'), '--previous-release', str(previous),
               '--public-key', str(args.public_key), '--keep-work']
    for v in args.base_versions.split():
        snapshot = fetched(server, v, work / ('base-' + v + '.json'))
        value = verified_release(snapshot, args.public_key)
        require_version(value, v)
        command += ['--base-release', v + '=' + str(snapshot)]
    if args.build_only:
        command.append('--build-only')
    if args.mandatory:
        command += ['--mandatory', '--mandatory-reason', args.mandatory_reason]
    elif args.mandatory_reason:
        raise ValueError('mandatory reason requires --mandatory')
    subprocess.run(command, check=True, cwd=ROOT)
    result = dict(status='BUILT_ONLY' if args.build_only else 'BUILT_AND_STAGED')
    if not args.build_only:
        result['stage'] = stage_objects(server, work / 'build/unsigned-release.json',
                                        work / 'build/work/publish/objects', work / 'upload')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['installer', 'promote', 'cleanup', 'stage', 'build'])
    p.add_argument('--version', required=True)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--public-key', type=Path, default=PUBLIC)
    p.add_argument('--local-server-root', type=Path)
    p.add_argument('--ssh-identity', type=Path)
    p.add_argument('--compiler', type=Path)
    p.add_argument('--latest', action='store_true')
    p.add_argument('--unsigned', type=Path)
    p.add_argument('--objects', type=Path)
    p.add_argument('--notes', default='')
    p.add_argument('--baseline')
    p.add_argument('--base-versions', default='2.0.2')
    p.add_argument('--build-only', action='store_true')
    p.add_argument('--mandatory', action='store_true')
    p.add_argument('--mandatory-reason', default='')
    args = p.parse_args()
    server_ops.version(args.version)
    args.work.mkdir(parents=True, exist_ok=True)
    server = LocalServer(args.local_server_root) if args.local_server_root else SSHServer(
        args.work / 'ssh', identity=args.ssh_identity)
    if args.action == 'installer':
        compiler = args.compiler or provision_inno(args.work / 'compiler')
        result = installer(server, args.version, args.work, compiler, public_key=args.public_key, latest=args.latest)
    elif args.action == 'promote':
        result = promote(server, args.version, args.work, public_key=args.public_key)
    elif args.action == 'cleanup':
        result = server.call('cleanup', args.version)
    elif args.action == 'stage':
        if not args.unsigned or not args.objects:
            p.error('stage requires --unsigned and --objects')
        if json.loads(args.unsigned.read_bytes())['version'] != args.version:
            p.error('unsigned version mismatch')
        result = stage_objects(server, args.unsigned, args.objects, args.work)
    else:
        result = build(server, args)
    (args.work / (args.action + '-result.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
