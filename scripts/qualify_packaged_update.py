"""Build evidence/local signed HTTP host for real frozen UAT payloads.

No installation, elevation, registration, production endpoint or user data access.
Target is a manifest plus tiny overlay, not a second copied distribution. The real
downloader independently reconstructs its staging tree as it would in production.
"""
import argparse
import base64
import copy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
from desktop.update_protocol import canonical, digest, MANIFEST_PATH, verify_release
from scripts import verify_windows_package as v

ROOT = Path('D:/CodexScratch/MentorFlow/MF-E107-UPDATER-PACKAGED-E2E-QUALIFY-20260907-05')
PROJECT = Path(__file__).resolve().parents[1]


def identities():
    def git(*args): return subprocess.check_output(['git', *args], cwd=PROJECT, text=True).strip()
    return dict(source_sha=git('rev-parse','HEAD'),tree_sha=git('rev-parse','HEAD^{tree}'))


def prepare():
    ROOT.mkdir(parents=True,exist_ok=True)
    public=ROOT/'public.json'; private=ROOT/'uat-private.key'
    if public.exists() or private.exists(): raise ValueError('existing UAT trust preserved')
    key=Ed25519PrivateKey.generate()
    private.write_bytes(key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()))
    public.write_bytes(canonical(dict(schema='mentorflow.packaged-update-uat.v1',root=str(ROOT),
        public_key=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode(),
        key_id='e107-packaged-uat-only',**identities())))
    print(json.dumps(dict(public_config=str(public),private_key='SCRATCH_ONLY_NOT_PRINTED',**identities())))


def finalize():
    app=ROOT/'base/App'; check=app/'SELF-CHECK'; check.mkdir(exist_ok=True)
    if (check/'package-manifest.json').exists(): raise ValueError('existing manifest preserved')
    for source,name in ((app/'MentorFlowSelfCheck.exe','MentorFlowSelfCheck.exe'),
                        (PROJECT/'scripts/verify_windows_package.py','verify_windows_package.py'),
                        (PROJECT/'scripts/mentorflow-check.bat','mentorflow-check.bat'),
                        (PROJECT/'scripts/mentorflow-update.bat','mentorflow-update.bat')):
        shutil.copyfile(source,check/name)
    shutil.copyfile(PROJECT/'mentorflow-version.json',app/'mentorflow-version.json')
    (check/'source-identity.json').write_bytes(canonical(identities()))
    from scripts.gui_bootstrap_trust import REQUIRED
    closure=set(REQUIRED)
    closure.update(p.relative_to(app).as_posix() for p in (app/'_internal/PySide6/plugins/platforms').glob('*') if p.is_file())
    closure.update(p.relative_to(app).as_posix() for p in (app/'_internal').glob('python*.dll'))
    (check/'gui-bootstrap-closure.json').write_bytes(canonical(dict(schema='mentorflow.gui-bootstrap-closure.v1',files=sorted(closure))))
    manifest=v.build_manifest(app,version_file=app/'mentorflow-version.json')
    doc=json.loads(manifest.read_bytes()); manifest.write_bytes(canonical(doc))
    from scripts.prepare_transactional_installer import verify
    _,rows=verify(app,v.sha256_file(manifest))
    evidence=dict(app=str(app/'MentorFlow.exe'),app_sha256=v.sha256_file(app/'MentorFlow.exe'),
        manifest=str(manifest),manifest_sha256=v.sha256_file(manifest),files=len(rows),
        real_base_payload_bytes=sum(row['size'] for row in rows.values()),**identities())
    (ROOT/'base-evidence.json').write_bytes(canonical(evidence)); print(json.dumps(evidence))


def run(case):
    app=ROOT/'base/App'; base=json.loads((app/MANIFEST_PATH).read_bytes())
    public=json.loads((ROOT/'public.json').read_bytes())
    key=Ed25519PrivateKey.from_private_bytes((ROOT/'uat-private.key').read_bytes())
    keys={public['key_id']:base64.b64decode(public['public_key'])}
    target=copy.deepcopy(base); target['version']='1.1.1'
    overlay=ROOT/'overlay'; overlay.mkdir(exist_ok=True)
    contents={}
    for path in ('mentorflow-version.json','_internal/mentorflow-version.json'):
        doc=json.loads((app/path).read_bytes()); doc['version']='1.1.1'
        contents[path]=canonical(doc)
    contents['_internal/update-uat/version-visible.txt']=b'MentorFlow packaged target 1.1.1\n'
    rows={r['path']:r for r in target['files']}
    for path,content in contents.items():
        rows[path]=dict(path=path,size=len(content),sha256=digest(content))
    target['files']=sorted(rows.values(),key=lambda r:r['path'])
    contents[MANIFEST_PATH]=canonical(target)
    rows[MANIFEST_PATH]=dict(path=MANIFEST_PATH,size=len(contents[MANIFEST_PATH]),sha256=digest(contents[MANIFEST_PATH]))
    # Asset HTTP serving reads existing full payload files or tiny immutable
    # overlays only; no fixture substitutes for the real packaged runtime.
    sources={}
    for path,row in rows.items():
        if path in contents:
            dest=overlay/row['sha256']
            if dest.exists() and dest.read_bytes()!=contents[path]: raise ValueError('overlay collision')
            if not dest.exists(): dest.write_bytes(contents[path])
            sources[row['sha256']]=dest
        else: sources[row['sha256']]=app/path
    counters=dict(metadata=0,assets=0,headers=0,requests=[])
    state={'raw':b''}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_GET(self):
            metadata=self.path=='/stable.json'
            path=sources.get(self.path.removeprefix('/objects/')) if self.path.startswith('/objects/') else None
            if not metadata and path is None: self.send_error(404); return
            body=state['raw'] if metadata else None
            size=len(body) if metadata else path.stat().st_size
            range_header=self.headers.get('Range','')
            offset=int(range_header[6:-1]) if range_header.startswith('bytes=') and range_header.endswith('-') else 0
            if not 0 <= offset < size: self.send_error(416); return
            counters['requests'].append(dict(path=self.path,offset=offset))
            self.send_response(206 if offset else 200)
            self.send_header('Content-Length',str(size-offset))
            if offset: self.send_header('Content-Range',f'bytes {offset}-{size-1}/{size}')
            counters['headers']+=sum(len(p) for p in self._headers_buffer)+2
            self.end_headers()
            stream=None
            try:
                if not metadata: stream=path.open('rb'); stream.seek(offset)
                position=offset
                while position<size:
                    block=body[position:position+32768] if metadata else stream.read(32768)
                    if not block: break
                    if case=='corrupt' and not metadata and position==0: block=b'X'+block[1:]
                    if case=='cancel' and not metadata: time.sleep(.03)
                    self.wfile.write(block); self.wfile.flush()
                    counters['metadata' if metadata else 'assets']+=len(block); position+=len(block)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass
            finally:
                if stream: stream.close()
    port_file=ROOT/'feed-port.json'
    port=json.loads(port_file.read_bytes())['port'] if port_file.exists() else 0
    if type(port) is not int or port and not 1024 <= port <= 65535:
        raise ValueError('invalid local feed port')
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler); server.daemon_threads=True
    if not port_file.exists(): port_file.write_bytes(canonical(dict(port=server.server_port)))
    origin=f'http://127.0.0.1:{server.server_port}'
    now=datetime.now(timezone.utc)
    signed=dict(schema_version=1,app_id='MentorFlow',channel='stable',version='1.1.1',metadata_version=2,
        issued_at=now.isoformat(),published_at=now.isoformat(),expires_at=(now+timedelta(days=1)).isoformat(),
        key_id=public['key_id'],rollout_percentage=100,rollout_seed='packaged-uat',withdrawn=False,
        min_supported_version='1.0.0',min_updater_version='1.0.0',mandatory=False,mandatory_reason='',
        release_notes='UAT 1.1.1: small visible version resource; unchanged real runtime retained.',
        full_release=dict(format='file-set-v1',size=sum(r['size'] for r in rows.values()),
            assets=[dict(**r,url=origin+'/objects/'+r['sha256']) for r in rows.values()]),
        incremental_assets=list(rows),file_manifest=target,hash_algorithm='sha256',signature_algorithm='ed25519',
        base_versions=[] if case=='fallback' else [base['version']],**identities())
    # A feed cannot reissue different signed bytes at the same epoch merely
    # because its host restarted. Keep source URLs and timestamps immutable
    # across cancel/resume; security cases deliberately derive new invalid data.
    template=ROOT/'release-template.json'
    if template.exists():
        signed=json.loads(template.read_bytes())
    else:
        template.write_bytes(canonical(signed))
    if case=='fallback': signed['base_versions']=[]
    if case=='expired': signed['expires_at']=(now-timedelta(seconds=1)).isoformat()
    if case=='replay': signed['metadata_version']=1
    envelope=dict(signed=signed,key_id=public['key_id'],signature=base64.b64encode(key.sign(canonical(signed))).decode())
    if case=='tampered': envelope['signed']['release_notes']+='TAMPERED'
    state['raw']=canonical(envelope)
    if case not in {'tampered','expired'}: verify_release(state['raw'],keys,current=base['version'])
    result_path=ROOT/('result-'+case+'.json')
    if result_path.exists(): raise ValueError('existing case evidence preserved')
    (ROOT/'request.json').write_bytes(canonical(dict(port=server.server_port,case=case)))
    (ROOT/('release-'+case+'.json')).write_bytes(state['raw'])
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    if case=='offline': server.shutdown(); server.server_close()
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',TEMP=str(ROOT),TMP=str(ROOT))
    started=time.monotonic()
    try:
        result=subprocess.run([str(app/'MentorFlow.exe'),'--qualify-packaged-updater'],env=env,
            cwd=app,timeout=265,creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        if case!='offline': server.shutdown(); server.server_close()
        thread.join(timeout=2)
    base_rows={r['path']:r for r in base['files']}
    changed=[p for p,r in rows.items() if base_rows.get(p)!=r]
    total=sum(r['size'] for r in rows.values())
    evidence=dict(case=case,process_exit_code=result.returncode,elapsed_seconds=time.monotonic()-started,
        real_full_release_bytes=total,real_changed_file_count=len(changed),
        real_changed_payload_bytes=sum(rows[p]['size'] for p in changed),
        asset_response_bytes=counters['assets'],metadata_response_bytes=counters['metadata'],
        total_http_body_bytes=counters['assets']+counters['metadata'],http_response_header_bytes=counters['headers'],
        saved_body_bytes=total-counters['assets']-counters['metadata'],
        body_saving_percent=100*(total-counters['assets']-counters['metadata'])/total,
        requests=counters['requests'],result=str(result_path),target_manifest_sha256=rows[MANIFEST_PATH]['sha256'])
    (ROOT/('http-'+case+'.json')).write_bytes(canonical(evidence))
    print(json.dumps(evidence))
    if result.returncode or not result_path.exists(): raise RuntimeError('packaged probe did not complete')


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('action',choices=['prepare','finalize','run'])
    parser.add_argument('--case',default='happy',choices=['happy','tampered','expired','replay','corrupt','cancel','resume','offline','fallback'])
    args=parser.parse_args()
    if args.action=='prepare': prepare()
    elif args.action=='finalize': finalize()
    else: run(args.case)
