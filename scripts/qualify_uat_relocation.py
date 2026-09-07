"""One-build, no-UAC, three-root frozen relocation/health qualification.

All destinations must be new. Existing source bundles and receipts are never
modified. Hardlinks are only between the new qualified bundle and its replicas;
no shared file is changed after link materialization.
"""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import types
import uuid

from desktop.update_protocol import canonical, digest, SecurityError
from desktop.uat_health_contract import verify_request, validate_health


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def comparable(code):
    if isinstance(code, types.CodeType):
        return (code.co_code, code.co_names, code.co_varnames, tuple(comparable(c) for c in code.co_consts))
    return code


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--analysis-toc', type=Path, required=True)
    p.add_argument('--roots', nargs=3, type=Path, required=True)
    args = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    from scripts.runtime_path_budget import budget
    from desktop.update_tree_security import normal
    work, base = normal(args.work), normal(args.base)
    roots = [normal(r) for r in args.roots]
    if len(set(roots)) != 3 or any(root == base or root in base.parents or base in root.parents or root.exists() for root in roots) or work.exists():
        raise RuntimeError('new separate qualification roots required')
    prior_raw = (base/'App/SELF-CHECK/package-manifest.json').read_bytes()
    prior_manifest = json.loads(prior_raw)
    paths = [row['path'] for row in prior_manifest['files']]
    for root in roots:
        budget(root, paths)
    product_budget = budget('E:/MentorFlow', paths)  # calculation only
    work.mkdir(parents=True)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    private = Ed25519PrivateKey.generate()
    pub = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    # Disposable fixture key, not a release/production credential.
    (work/'fixture-private.key').write_bytes(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    (work/'mentorflow_uat_build_authority.py').write_text(
        "BUILD_MARKER = 'mentorflow.test-only.signed-attempt.v1'\nPUBLIC_KEYS = {'relocation-fixture': " + repr(pub) + '}\n', encoding='utf-8')
    env = dict(os.environ, MF_UAT_PROJECT=str(repo), MF_UAT_BUILD=str(work),
        MF_UAT_ANALYSIS_TOC=str(args.analysis_toc), PYTHONDONTWRITEBYTECODE='1',
        PYINSTALLER_CONFIG_DIR='E:/Codex/Data/cache/pyinstaller', TEMP=str(work), TMP=str(work))
    source_sha = subprocess.check_output(['git','rev-parse','HEAD'], cwd=repo, text=True).strip()
    tree_sha = subprocess.check_output(['git','rev-parse','HEAD^{tree}'], cwd=repo, text=True).strip()
    identity = dict(source_sha=source_sha, tree_sha=tree_sha, build_count=1)
    with (work/'build.log').open('x', encoding='utf-8') as log:
        result = subprocess.run([sys.executable, '-B', '-m', 'PyInstaller', '--noconfirm',
            '--workpath', str(work/'build'), '--distpath', str(work/'dist'),
            str(repo/'release/uat-relocation.spec')], env=env, cwd=repo, stdout=log, stderr=subprocess.STDOUT, timeout=600)
    if result.returncode:
        raise RuntimeError('single UAT build failed; inspect build.log, no rebuild')
    exe = work/'build/uat-relocation/MentorFlow.exe'
    from PyInstaller.archive.readers import CArchiveReader
    from PyInstaller.utils.win32.winmanifest import read_manifest_from_executable
    import xml.etree.ElementTree as ET
    xml = ET.fromstring(read_manifest_from_executable(str(exe)))
    assert [node.attrib.get('level') for node in xml.iter() if node.tag.rsplit('}',1)[-1]=='requestedExecutionLevel'] == ['asInvoker']
    assert any(node.tag.rsplit('}',1)[-1]=='longPathAware' and node.text=='true' for node in xml.iter())
    archive = CArchiveReader(str(exe))
    pyz = archive.open_embedded_archive(next(k for k in archive.toc if k.endswith('.pyz')))
    for name in ('desktop.uat_health_contract','desktop.packaged_update_uat','desktop.update_tree_security','desktop.update_download'):
        assert comparable(pyz.extract(name)) == comparable(compile((repo/(name.replace('.','/')+'.py')).read_bytes(),name,'exec'))
    assert 'mentorflow_uat_build_authority' in pyz.toc
    print('BUILD_AND_FROZEN_BYTECODE_PASS', flush=True)
    a = roots[0]/'App'
    # Real independent copy from the prior immutable delivery. No linking back
    # to the old 08 base, whose files also belong to previous evidence trees.
    shutil.copytree(base/'App', a)
    shutil.copyfile(exe, a/'MentorFlow.exe')
    (a/'_internal/update-uat/public.json').write_bytes(canonical(dict(
        schema='mentorflow.packaged-update-uat.v2', key_id='relocation-fixture',
        public_key=base64.b64encode(pub).decode(), source_sha=source_sha, tree_sha=tree_sha)))
    from scripts.verify_windows_package import build_manifest
    build_manifest(a)
    raw = (a/'SELF-CHECK/package-manifest.json').read_bytes()
    manifest = json.loads(raw)
    pin, exe_pin = digest(raw), sha(a/'MentorFlow.exe')
    identity.update(exe_sha256=exe_pin, manifest_sha256=pin, main_execution_level='asInvoker', longPathAware=True)
    (work/'build-identity.json').write_bytes(canonical(identity))
    # New hardlink replicas are used solely read-only by frozen Qt probes.
    for root in roots[1:]:
        shutil.copytree(a, root/'App', copy_function=os.link)
    print('THREE_BUNDLES_MATERIALIZED', flush=True)
    measurements, negatives = [], []
    def launch(root, mutation=None):
        attempt = uuid.uuid4().hex
        control = work/'attempts'/attempt
        control.mkdir(parents=True)
        app = root/'App'
        request = dict(schema_version=1, app_id='MentorFlow', attempt_id=attempt,
            nonce=os.urandom(32).hex(), purpose='boot-health', created_at=time.time(), expires_at=time.time()+100,
            expected_app_root=str(app), root_identity=[app.stat().st_dev,app.stat().st_ino],
            runtime_version=manifest['version'], target_manifest_sha256=pin, executable_sha256=exe_pin,
            signed_release_identity=digest(canonical(dict(app_id='MentorFlow', version=manifest['version'], manifest_sha256=pin))),
            receipt_sha256=None, health_endpoint=str(control/'health.json'), fixture_root=None, cross_volume_root=None)
        expected = copy.deepcopy(request)
        if mutation: mutation(request)
        signed = canonical(dict(signed=request,key_id='relocation-fixture',signature=base64.b64encode(private.sign(canonical(request))).decode()))
        request_path = control/'request.json'
        request_path.write_bytes(signed)
        app_env = dict(env, MENTORFLOW_UAT_ATTEMPT=str(request_path), QT_QPA_PLATFORM='offscreen',
            QT_QPA_PLATFORM_PLUGIN_PATH=str(app/'_internal/PySide6/plugins/platforms'), TEMP=str(control), TMP=str(control))
        start = time.perf_counter()
        with (control/'process.log').open('xb') as log:
            process = subprocess.Popen([str(app/'MentorFlow.exe'),'--qualify-packaged-startup'],env=app_env,cwd=app,stdout=log,stderr=subprocess.STDOUT)
            try: code = process.wait(timeout=55)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
                raise RuntimeError('owned frozen probe timed out; no retry')
        endpoint = control/'health.json'
        result = dict(root=str(root), pid=process.pid, exit_code=code, elapsed_ms=(time.perf_counter()-start)*1000,
            evidence=str(endpoint), exe_sha256=sha(app/'MentorFlow.exe'), manifest_sha256=sha(app/'SELF-CHECK/package-manifest.json'))
        if mutation:
            if endpoint.exists():
                try: validate_health(json.loads(endpoint.read_bytes()),expected,expected_pid=process.pid)
                except SecurityError: result['rejected']='VALIDATOR_REJECT'
                else: raise AssertionError('incorrect signed attempt accepted by validator')
            else:
                assert code != 0
                result['rejected']='PACKAGED_PRE_QT_REJECT'
        else:
            assert code == 0
            health = json.loads(endpoint.read_bytes())
            verify_request(signed, {'relocation-fixture':pub})
            validate_health(health, expected, expected_pid=process.pid)
            result.update(health=health, ready_ms=health['marks']['QT_EVENT_LOOP_READY'])
            # Same real-process evidence, not synthetic success markers.
            for field, value in [('nonce','0'*64),('runtime_version','99.0.0'),('target_manifest_sha256','0'*64),('timestamp',expected['created_at']-1),('process_id',process.pid+1)]:
                bad = copy.deepcopy(health); bad[field]=value
                try: validate_health(bad,expected,expected_pid=process.pid)
                except SecurityError: pass
                else: raise AssertionError('bad packaged health accepted: '+field)
        return result
    for root in roots:
        measurements.append(launch(root))
        print('QT_READY '+str(root), flush=True)
    cases = {
        'wrong_nonce': lambda r:r.update(nonce='0'*64),
        'wrong_version': lambda r:r.update(runtime_version='99.0.0'),
        'wrong_manifest': lambda r:r.update(target_manifest_sha256='0'*64),
        'stale_attempt': lambda r:r.update(created_at=time.time()-200, expires_at=time.time()-100)}
    for name, mutation in cases.items():
        negatives.append(dict(case=name, **launch(roots[0],mutation)))
    assert sha(base/'App/MentorFlow.exe') == '7342881923ab4869a5c44faee4b6b11f89ad2b11057eae5a33c7cc36a853b970'
    assert digest((base/'App/SELF-CHECK/package-manifest.json').read_bytes()) == digest(prior_raw)
    result = dict(status='PASS_REAL_PACKAGED',build=identity,roots=measurements,negative_contracts=negatives,
        same_exe_sha_all_roots=True, same_manifest_all_roots=True, product_default_root_compatible=product_budget,
        scope='Offscreen real frozen Qt event-loop health; TEST-ONLY fixture, not protected install/commit health authority',
        UAC=0, formal_data_writes=0, formal_install_writes=0, previous_package_preserved=True)
    (work/'relocation-result.json').write_bytes(canonical(result))
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    main()
