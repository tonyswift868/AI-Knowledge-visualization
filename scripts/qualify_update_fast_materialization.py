"""Dispatch-07 short-root real packaged qualification; never install or apply."""
import argparse
import base64
import copy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop.update_protocol import canonical,digest,MANIFEST_PATH,verify_release
from scripts import qualify_packaged_update as q
from scripts.runtime_path_budget import budget,runtime_root

ROOT=Path('D:/MFU7')
PRIOR=Path('D:/CodexScratch/MentorFlow/MF-E107-UPDATER-REBUILD-STARTUP-PERF-20260907-06')
q.ROOT=ROOT


def prepare():
    q.prepare()
    source=PRIOR/'base/App'
    shutil.copytree(source,ROOT/'base/App',ignore=lambda directory,names:
        {'Installer'} if Path(directory)==source else set())


def finalize(exe):
    app=ROOT/'base/App'
    public=json.loads((ROOT/'public.json').read_bytes()); public.update(q.identities())
    (ROOT/'public.json').write_bytes(canonical(public))
    shutil.copyfile(ROOT/'public.json',app/'_internal/update-uat/public.json')
    shutil.copyfile(exe,app/'MentorFlow.exe')
    manifest=json.loads((app/MANIFEST_PATH).read_bytes())
    for row in manifest['files']:
        if row['path'] in {'MentorFlow.exe','_internal/update-uat/public.json'}:
            data=(app/row['path']).read_bytes(); row.update(size=len(data),sha256=digest(data))
    (app/MANIFEST_PATH).write_bytes(canonical(manifest))
    target=copy.deepcopy(manifest); target['version']='1.1.1'
    contents={}
    for path in ('mentorflow-version.json','_internal/mentorflow-version.json'):
        value=json.loads((app/path).read_bytes()); value['version']='1.1.1'; contents[path]=canonical(value)
    contents['_internal/update-uat/version-visible.txt']=b'MentorFlow packaged target 1.1.1\n'
    rows={r['path']:r for r in target['files']}
    for path,data in contents.items(): rows[path]=dict(path=path,size=len(data),sha256=digest(data))
    target['files']=sorted(rows.values(),key=lambda row:row['path'])
    contents[MANIFEST_PATH]=canonical(target)
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(contents[MANIFEST_PATH]),sha256=digest(contents[MANIFEST_PATH]))
    signed=json.loads((PRIOR/'release-template.json').read_bytes())
    now=datetime.now(timezone.utc)
    signed.update(file_manifest=target,issued_at=now.isoformat(),published_at=now.isoformat(),
        expires_at=(now+timedelta(days=1)).isoformat(),**q.identities())
    signed['full_release']=dict(format='file-set-v1',size=sum(r['size'] for r in rows.values()),
        assets=[dict(**row,url='http://127.0.0.1:55072/objects/'+row['sha256']) for row in rows.values()])
    signed['incremental_assets']=list(rows)
    key=Ed25519PrivateKey.from_private_bytes((ROOT/'uat-private.key').read_bytes())
    raw=canonical(dict(signed=signed,key_id=public['key_id'],signature=base64.b64encode(key.sign(canonical(signed))).decode()))
    verify_release(raw,{public['key_id']:base64.b64decode(public['public_key'])},current='1.1.0')
    if (ROOT/'release-happy.json').exists(): raise RuntimeError('existing release preserved')
    (ROOT/'release-happy.json').write_bytes(raw)
    objects=ROOT/'c/objects'; objects.mkdir(parents=True)
    for path,data in contents.items():
        item=objects/digest(data)
        if not item.exists(): item.write_bytes(data)
    (ROOT/'build-identity.json').write_bytes(canonical(dict(**q.identities(),
        exe_sha256=digest((app/'MentorFlow.exe').read_bytes()),
        manifest_sha256=digest((app/MANIFEST_PATH).read_bytes()),path_budget=budget(app.parent,rows))))


def launch(app,argument,timeout):
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',TEMP=str(ROOT),TMP=str(ROOT))
    start=time.monotonic()
    process=subprocess.Popen([str(app/'MentorFlow.exe'),argument],cwd=app,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
    create=(time.monotonic()-start)*1000
    try: code=process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill(); process.wait(); raise
    if code: raise RuntimeError('packaged child failed: '+str(code))
    return start,create,code


def startup(case,app):
    rows=json.loads((ROOT/'release-happy.json').read_bytes())['signed']['file_manifest']['files']
    maximum=runtime_root(app,[r['path'] for r in rows])
    (ROOT/'startup-request.json').write_bytes(canonical(dict(case=case)))
    start,create,code=launch(app,'--qualify-packaged-startup',20)
    result=json.loads((ROOT/('startup-'+case+'.json')).read_bytes())
    result.update(process_create_ms=create,process_exit_code=code,longest_runtime_path=maximum,
        phases_ms={k:(v-start)*1000 for k,v in result['marks'].items()})
    (ROOT/('startup-'+case+'-runner.json')).write_bytes(canonical(result))
    assert result['qt_event_loop_ready'] and result['phases_ms']['WINDOW_READY']<=20000
    print(json.dumps(result),flush=True)


def qualify():
    launch(ROOT/'base/App','--qualify-fast-materialization',180)
    result=json.loads((ROOT/'fast-result.json').read_bytes())
    app=Path(str(result['target']).removeprefix('\\\\?\\'))
    if not app.is_relative_to(ROOT): raise RuntimeError('unexpected target root')
    startup('reconstructed',app)
    # Move this owned directory on the same volume, not another 2 GB copy.
    # The source App and every shared file object remain unmodified.
    maximum=result['path_budget']['max_supported_install_root_length']
    near=ROOT/('n'*(maximum-len(str(ROOT))-1))
    destination=near/'.u'/app.parent.name/'App'
    if not destination.is_relative_to(ROOT) or destination.exists(): raise RuntimeError('unsafe near-budget target')
    destination.parent.mkdir(parents=True)
    app.rename(destination)
    try: startup('near-limit',destination)
    finally:
        if app.exists(): raise RuntimeError('original target path replaced')
        destination.rename(app)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['prepare','finalize','qualify'])
    parser.add_argument('--exe',type=Path); args=parser.parse_args()
    if args.action=='prepare': prepare()
    elif args.action=='finalize': finalize(args.exe)
    else: qualify()
