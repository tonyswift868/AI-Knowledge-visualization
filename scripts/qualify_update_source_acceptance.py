"""Dispatch 08: bounded standard-user packaged qualification; no apply/install."""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop.update_protocol import canonical, digest, MANIFEST_PATH, verify_release
from scripts import qualify_update_fast_materialization as prior

ROOT=Path('D:/MFU8')
prior.ROOT=ROOT
prior.q.ROOT=ROOT


def finalize(exe):
    prior.q.prepare()
    prior.finalize(exe)
    from scripts.windows_install_trust import SCHEMA
    installed=ROOT/'base'; app_stat=(installed/'App').stat(); root_stat=installed.stat()
    manifest=json.loads((installed/'App'/MANIFEST_PATH).read_bytes())
    receipt=dict(full_verified=True,binding=dict(schema=SCHEMA,root=str(installed),
        version=manifest['version'],manifest_sha256=digest((installed/'App'/MANIFEST_PATH).read_bytes()),
        root_identity=[root_stat.st_dev,root_stat.st_ino]),
        inventory={'App':['dir',0,app_stat.st_mtime_ns,app_stat.st_ctime_ns,app_stat.st_dev,app_stat.st_ino,0]})
    # Explicit synthetic UAT receipt at the same receipt location. It is NOT
    # admin-owned, cannot qualify protected(), and grants no installation trust.
    (installed/'installation-receipt.json').write_bytes(canonical(receipt))
    # Independently copied representative real packaged bytes, not shared with
    # the 2 GB base. The signing key stays in this isolated scratch root.
    app=ROOT/'subset/install/App'; app.mkdir(parents=True)
    base=ROOT/'base/App'
    keep={'MentorFlow.exe','_internal/python313.dll','mentorflow-version.json'}
    manifest=json.loads((base/MANIFEST_PATH).read_bytes())
    manifest['files']=[r for r in manifest['files'] if r['path'] in keep]
    for row in manifest['files']:
        dest=app/row['path']; dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(base/row['path'],dest)
    (app/MANIFEST_PATH).parent.mkdir(parents=True)
    (app/MANIFEST_PATH).write_bytes(canonical(manifest))
    signed=json.loads((ROOT/'release-happy.json').read_bytes())['signed']
    target=copy.deepcopy(manifest); target['version']='1.1.1'
    version=json.loads((app/'mentorflow-version.json').read_bytes()); version['version']='1.1.1'
    content={'mentorflow-version.json':canonical(version)}
    for row in target['files']:
        if row['path'] in content:
            data=content[row['path']]; row.update(size=len(data),sha256=digest(data))
    content[MANIFEST_PATH]=canonical(target)
    rows={r['path']:r for r in target['files']}
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(content[MANIFEST_PATH]),sha256=digest(content[MANIFEST_PATH]))
    signed['file_manifest']=target
    signed['full_release']=dict(format='file-set-v1',size=sum(r['size'] for r in rows.values()),
        assets=[dict(**r,url='http://127.0.0.1:55072/objects/'+r['sha256']) for r in rows.values()])
    signed['incremental_assets']=list(rows)
    public=json.loads((ROOT/'public.json').read_bytes())
    key=Ed25519PrivateKey.from_private_bytes((ROOT/'uat-private.key').read_bytes())
    raw=canonical(dict(signed=signed,key_id=public['key_id'],signature=base64.b64encode(key.sign(canonical(signed))).decode()))
    verify_release(raw,{public['key_id']:base64.b64decode(public['public_key'])},current='1.1.0')
    (ROOT/'subset/release.json').write_bytes(raw)
    objects=ROOT/'subset/cache/objects'; objects.mkdir(parents=True)
    for data in content.values(): (objects/digest(data)).write_bytes(data)


def qualify():
    prior.launch(ROOT/'base/App','--qualify-cross-volume',120)
    prior.launch(ROOT/'base/App','--qualify-fast-materialization',120)
    result=json.loads((ROOT/'fast-result.json').read_bytes())
    app=Path(str(result['target']).removeprefix('\\\\?\\'))
    prior.startup('reconstructed',app)
    # Read-only full current rehash outside the performance measurement.
    before=json.loads((ROOT/'base/App'/MANIFEST_PATH).read_bytes())
    for row in before['files']:
        with (ROOT/'base/App'/row['path']).open('rb') as stream:
            import hashlib
            if hashlib.file_digest(stream,'sha256').hexdigest()!=row['sha256']:
                raise RuntimeError('previous payload modified: '+row['path'])
    (ROOT/'previous-preserved.json').write_bytes(canonical(dict(all_manifest_hashes='PASS',files=len(before['files']))))
    print(json.dumps(dict(acceptance_ms=result['acceptance_ms'],rebuild_ms=result['rebuild_ms'],
        total_ms=result['acceptance_ms']+result['rebuild_ms'])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['finalize','qualify'])
    parser.add_argument('--exe',type=Path); args=parser.parse_args()
    if args.action=='finalize': finalize(args.exe)
    else: qualify()
