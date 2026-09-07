"""Finite dispatch-06 packaged performance probe; no install/apply authority.

Reuses dispatch-05's actual onedir dependencies and HTTP host, never its evidence
paths. All outputs and mutations are confined to the explicit dispatch-06 UAT
root. A's original EXE has no entry markers: its event-loop completion is an
upper bound, not an invented internal phase timestamp.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

from desktop.update_protocol import canonical, digest, MANIFEST_PATH
from scripts import qualify_packaged_update as q

ROOT = Path('D:/CodexScratch/MentorFlow/MF-E107-UPDATER-REBUILD-STARTUP-PERF-20260907-06')
PRIOR = Path('D:/CodexScratch/MentorFlow/MF-E107-UPDATER-PACKAGED-E2E-QUALIFY-20260907-05')
q.ROOT = ROOT


def save(name, result):
    path = ROOT/name
    if path.exists():
        raise RuntimeError('existing evidence preserved: '+str(path))
    path.write_bytes(canonical(result))


def environment():
    return dict(os.environ, QT_QPA_PLATFORM='offscreen', TEMP=str(ROOT), TMP=str(ROOT))


def prepare():
    q.prepare()
    shutil.copytree(PRIOR/'base/App', ROOT/'base/App')
    shutil.copyfile(ROOT/'public.json',ROOT/'base/App/_internal/update-uat/public.json')
    # Original frozen base EXE, original runtime collection; only the public
    # UAT destination changes so prior evidence and all product data stay inert.
    app = ROOT/'base/App'
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0)); port = probe.getsockname()[1]
    save('request.json', dict(case='offline',port=port))
    start = time.monotonic()
    process = subprocess.Popen([str(app/'MentorFlow.exe'),'--qualify-packaged-updater'],
        cwd=app,env=environment(),creationflags=subprocess.CREATE_NO_WINDOW)
    create_ms = (time.monotonic()-start)*1000
    code = process.wait(timeout=25)
    elapsed = (time.monotonic()-start)*1000
    result = json.loads((ROOT/'result-offline.json').read_bytes())
    save('startup-original-base.json',dict(process_create_ms=create_ms,process_exit_code=code,
        window_ready_upper_bound_ms=elapsed, qt_event_loop_ready=result['qt_event_ticks']>0,
        packaged_ui_created=result['packaged_ui_created'],phase_markers='UNAVAILABLE_IN_ORIGINAL_EXE',
        exe_sha256=digest((app/'MentorFlow.exe').read_bytes())))


def update_base(exe):
    app = ROOT/'base/App'
    if not Path(exe).is_relative_to(ROOT):
        raise RuntimeError('dispatch build EXE required')
    public = json.loads((ROOT/'public.json').read_bytes())
    public.update(q.identities())
    (ROOT/'public.json').write_bytes(canonical(public))
    shutil.copyfile(ROOT/'public.json',app/'_internal/update-uat/public.json')
    shutil.copyfile(exe, app/'MentorFlow.exe')
    manifest = json.loads((app/MANIFEST_PATH).read_bytes())
    for row in manifest['files']:
        if row['path'] in {'MentorFlow.exe','_internal/update-uat/public.json'}:
            raw = (app/row['path']).read_bytes()
            row.update(size=len(raw),sha256=digest(raw))
    (app/MANIFEST_PATH).write_bytes(canonical(manifest))
    save('fixed-base-identity.json', dict(exe_sha256=digest((app/'MentorFlow.exe').read_bytes()),
        manifest_sha256=digest((app/MANIFEST_PATH).read_bytes()),**q.identities()))


def startup(case, app):
    if not app.is_relative_to(ROOT):
        raise RuntimeError('isolated tree required')
    (ROOT/'startup-request.json').write_bytes(canonical(dict(case=case)))
    start = time.monotonic()
    process = subprocess.Popen([str(app/'MentorFlow.exe'),'--qualify-packaged-startup'],
        cwd=app,env=environment(),creationflags=subprocess.CREATE_NO_WINDOW)
    create_ms = (time.monotonic()-start)*1000
    try:
        code = process.wait(timeout=25)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait()
        save('startup-'+case+'-runner.json',dict(process_create_ms=create_ms,
            process_exit_code=process.returncode,fatal_error='25s bound exceeded'))
        raise
    result = json.loads((ROOT/('startup-'+case+'.json')).read_bytes())
    result.update(process_create_ms=create_ms,process_exit_code=code,
        elapsed_ms=(time.monotonic()-start)*1000,
        phases_ms={k:(v-start)*1000 for k,v in result['marks'].items()})
    save('startup-'+case+'-runner.json',result)
    print(json.dumps(result),flush=True)


def abc():
    result = json.loads((ROOT/'result-happy.json').read_bytes())
    if result['snapshot']['state'] != 'READY_TO_APPLY':
        raise RuntimeError('reconstructed tree not verified READY')
    # Direct target originates in the build tree, not reconstructed output.
    direct = ROOT/'direct/App'
    signed = json.loads((ROOT/'release-happy.json').read_bytes())['signed']
    rows = {r['path']:r for r in signed['full_release']['assets']}
    if not direct.exists():
        # The outer Setup is a delivery attachment, not an App manifest entry.
        # Do not smuggle it into the direct target's exact signed file set.
        source_app = ROOT/'base/App'
        shutil.copytree(source_app,direct,ignore=lambda directory,names:
            {'Installer'} if Path(directory)==source_app else set())
        for path in ('mentorflow-version.json','_internal/mentorflow-version.json',
                     '_internal/update-uat/version-visible.txt',MANIFEST_PATH):
            destination=direct/path; destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/'overlay'/rows[path]['sha256'],destination)
    # Full explicit diagnostic verification finishes BEFORE startup timing.
    import base64
    from desktop.update_protocol import verify_release
    from desktop.update_download import ObjectCache
    public=json.loads((ROOT/'public.json').read_bytes())
    release=verify_release((ROOT/'release-happy.json').read_bytes(),
        {public['key_id']:base64.b64decode(public['public_key'])},current='1.1.0')
    cache=ObjectCache(ROOT,None)
    # The cache uses explicit Windows extended paths. Keep both operands in
    # that representation; do not weaken the verifier's containment check.
    cache.verify_tree(cache.root/'direct/App',release)
    save('direct-full-verify.json',dict(status='PASS',files=len(release.rows)))
    startup('base',ROOT/'base/App')
    startup('direct',direct)
    reconstructed=Path(str(result['staged']).removeprefix('\\\\?\\'))
    startup('reconstructed',reconstructed)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['prepare','base','rebuild','abc'])
    parser.add_argument('--exe',type=Path)
    args=parser.parse_args()
    if args.action=='prepare': prepare()
    elif args.action=='base': update_base(args.exe)
    elif args.action=='rebuild': q.run('happy')
    else: abc()
