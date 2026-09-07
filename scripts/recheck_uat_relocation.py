"""No-build check of an existing relocation artifact; bounded fixture correction.

The package path guard rejects hardlinked manifests. Materialization may share
payload files read-only but each replica's authority manifest must be independent.
This copies only those identical manifest bytes, never changes old delivery data.
"""
import argparse
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from desktop.update_protocol import canonical, SecurityError
from desktop.uat_health_contract import verify_request, validate_health
from scripts.qualify_uat_relocation import sha


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--roots',type=Path,nargs=3,required=True)
    args=parser.parse_args()
    work=args.work.absolute(); roots=[r.absolute() for r in args.roots]
    identity=json.loads((work/'build-identity.json').read_bytes())
    private=Ed25519PrivateKey.from_private_bytes((work/'fixture-private.key').read_bytes())
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
    keys={'relocation-fixture':private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)}
    correction=[]
    for root in roots:
        app=root/'App'; manifest=app/'SELF-CHECK/package-manifest.json'
        assert sha(app/'MentorFlow.exe')==identity['exe_sha256']
        assert sha(manifest)==identity['manifest_sha256']
        assert not manifest.lstat().st_file_attributes & 0x400
        if manifest.stat().st_nlink>1:
            temporary=manifest.parent/('independent-'+uuid.uuid4().hex+'.json')
            with temporary.open('xb') as out:
                out.write(manifest.read_bytes())
            temporary.replace(manifest)
            correction.append(str(manifest))
        assert sha(manifest)==identity['manifest_sha256'] and manifest.stat().st_nlink==1
    measurements=[]; negatives=[]
    for root in roots:
        app=root/'App'; manifest=json.loads((app/'SELF-CHECK/package-manifest.json').read_bytes())
        attempt=uuid.uuid4().hex; control=work/'recheck-attempts'/attempt; control.mkdir(parents=True)
        now=time.time()
        request=dict(schema_version=1,app_id='MentorFlow',attempt_id=attempt,nonce=os.urandom(32).hex(),
            purpose='boot-health',created_at=now,expires_at=now+100,
            expected_app_root=str(app),root_identity=[app.stat().st_dev,app.stat().st_ino],
            runtime_version=manifest['version'],target_manifest_sha256=identity['manifest_sha256'],
            executable_sha256=identity['exe_sha256'],signed_release_identity=identity['manifest_sha256'],
            receipt_sha256=None,health_endpoint=str(control/'health.json'),fixture_root=None,cross_volume_root=None)
        def sign(value):
            return canonical(dict(signed=value,key_id='relocation-fixture',signature=base64.b64encode(private.sign(canonical(value))).decode()))
        (control/'request.json').write_bytes(sign(request))
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',MENTORFLOW_UAT_ATTEMPT=str(control/'request.json'),
            QT_QPA_PLATFORM='offscreen',QT_QPA_PLATFORM_PLUGIN_PATH=str(app/'_internal/PySide6/plugins/platforms'),TEMP=str(control),TMP=str(control))
        start=time.perf_counter()
        with (control/'process.log').open('xb') as log:
            process=subprocess.Popen([str(app/'MentorFlow.exe'),'--qualify-packaged-startup'],env=env,cwd=app,stdout=log,stderr=subprocess.STDOUT)
            try: code=process.wait(timeout=50)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
                raise RuntimeError('owned packaged probe timeout; no further retry')
        assert code==0, code
        health=json.loads((control/'health.json').read_bytes())
        verify_request(sign(request),keys)
        validate_health(health,request,expected_pid=process.pid)
        measurements.append(dict(root=str(root),exe_sha256=sha(app/'MentorFlow.exe'),manifest_sha256=sha(app/'SELF-CHECK/package-manifest.json'),
            exit_code=code,elapsed_ms=(time.perf_counter()-start)*1000,ready_ms=health['marks']['QT_EVENT_LOOP_READY'],health=health,evidence=str(control/'health.json')))
        for field,value in [('nonce','0'*64),('runtime_version','99.0.0'),('target_manifest_sha256','0'*64),('expires_at',time.time()-1)]:
            expected=copy.deepcopy(request); expected[field]=value
            try: validate_health(health,expected,expected_pid=process.pid)
            except SecurityError as error:
                negatives.append(dict(root=str(root),field=field,result='REJECT',reason=str(error),scope='coordinator validator against actual same-EXE health and wrong expected contract'))
            else: raise AssertionError('incorrect expected health contract accepted')
        print('QT_READY '+str(root)+' '+str(measurements[-1]['ready_ms']),flush=True)
    assert len({r['exe_sha256'] for r in measurements})==1
    assert len({r['manifest_sha256'] for r in measurements})==1
    result=dict(status='PASS_REAL_PACKAGED',build=identity,roots=measurements,negative_contracts=negatives,
        same_exe_sha_all_roots=True,same_manifest_all_roots=True,manifest_replica_correction=correction,
        scope='Real frozen offscreen Qt event-loop health + coordinator-side wrong contract rejection; not installed authority',builds_this_recheck=0,UAC=0)
    with (work/'relocation-recheck-result.json').open('xb') as stream:stream.write(canonical(result))
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
