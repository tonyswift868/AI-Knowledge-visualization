"""Build one isolated real consumer BASE and signed tiny TARGET/bad overlays.

Only existing installed toolchain/dependency Analysis is reused. No installation,
UAC, server deployment, dependency installation or formal data is performed.
"""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from desktop.update_protocol import canonical, digest
from scripts import build_real_update_delivery as shared
from scripts import verify_windows_package as v

APP_ID = 'MentorFlow.UAT.PerUserCore2'


def build(args):
    if args.one_click_fixture and not args.core_only:
        raise ValueError('one-click loopback fixture can never build a distributable installer')
    work=args.work.absolute(); work.mkdir(exist_ok=True)
    shared.APP_ID=APP_ID
    identity=shared.source_identity()
    if args.source_commit:
        # A single-parent delivery commit may deliberately omit earlier cleanup
        # commits. Every task-owned source path must still match this worktree.
        excluded={'scripts/audit_e107_storage.py','scripts/cleanup_e107_build_scratch.ps1','scripts/test_audit_e107_storage.py'}
        differences=set(shared.git('diff','--name-only',args.source_commit,'HEAD').splitlines())
        if differences-excluded: raise ValueError('delivery source does not match current owned code')
        identity.update(source_sha=shared.git('rev-parse',args.source_commit),
                        tree_sha=shared.git('rev-parse',args.source_commit+'^{tree}'))
    directory=work/'build'/identity['source_sha'][:12]; directory.mkdir(parents=True)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
    keypath=work/'signing-test-only.key'
    if keypath.exists(): private=Ed25519PrivateKey.from_private_bytes(keypath.read_bytes())
    else:
        private=Ed25519PrivateKey.generate()
        with keypath.open('xb') as stream:stream.write(private.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()))
    public=private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
    authority=(f"APP_ID={APP_ID!r}\nPUBLIC_KEYS={{'per-user-uat':{public!r}}}\n"
        f"ONE_CLICK_TEST_FIXTURE={args.one_click_fixture!r}\n"
        "import os\nfrom pathlib import Path\n"
        "WORK_ROOT=str(Path(os.environ['LOCALAPPDATA'])/'MentorFlowPerUserCoreFreeze')\n"
        "DATA_ROOT=str(Path(WORK_ROOT)/'data')\n"
        f"SOURCE_SHA={identity['source_sha']!r}\n")
    (directory/'mentorflow_delivery_authority.py').write_text(authority,encoding='utf-8')
    env={**os.environ,'TEMP':str(work),'TMP':str(work),'PYTHONDONTWRITEBYTECODE':'1',
         'PYINSTALLER_CONFIG_DIR':'E:/Codex/Data/cache/pyinstaller',
         'MF_DELIVERY_PROJECT':str(shared.PROJECT),'MF_DELIVERY_BUILD':str(directory),'MF_DELIVERY_ANALYSIS':str(args.analysis),
         'MF_DELIVERY_SEED_PYZ_TOC':str(args.seed_pyz_toc) if args.seed_pyz_toc else '',
         'MF_DELIVERY_RUNTIME_ANALYSIS':str(directory/'pyi-user/UserRuntime/Analysis-00.toc')}
    cmd=[sys.executable,'-B','-m','PyInstaller','--onedir','--windowed','--name','UserRuntime',
         '--paths',str(shared.PROJECT),'--paths',str(directory),'--hidden-import','mentorflow_delivery_authority',
         '--distpath',str(directory/'native'),'--workpath',str(directory/'pyi-user'),
         '--specpath',str(directory),'--noupx']
    for module in ('torch','PySide6','numpy','pandas','matplotlib','scipy','transformers','docling','pytest','backend','desktop.main'):
        cmd+=['--exclude-module',module]
    cmd.append(str(shared.PROJECT/'scripts/per_user_install.py'))
    shared.run(cmd,shared.PROJECT,directory/'runtime.log',env)
    shared.run([sys.executable,'-B','-m','PyInstaller','--distpath',directory/'app','--workpath',directory/'pyi-app',
        shared.PROJECT/'release/real-delivery-app.spec'],shared.PROJECT,directory/'app.log',env)
    compiled=directory/'pyi-app/real-delivery-app/MentorFlow.exe'
    app=work/'release/App'
    from scripts.prepare_transactional_installer import verify
    if args.refresh:
        if (work/'build-evidence.json').exists():
            old=json.loads((work/'build-evidence.json').read_bytes())
        else:
            # A compiler failure can leave a fully signed, unpublished payload.
            # It remains an incomplete build, not a fabricated installer PASS.
            signed=json.loads((work/'release/base.json').read_bytes())['signed']
            old=dict(source=json.loads((app/'SELF-CHECK/source-identity.json').read_bytes()),
                     releases=[dict(manifest_sha256=digest(canonical(signed['file_manifest'])))],
                     status='INCOMPLETE_INSTALLER_BUILD')
        _,rows=verify(app,old['releases'][0]['manifest_sha256'])
        archive=work/'prior-candidate'/old['source']['source_sha'][:12]
        archive.mkdir(parents=True)
        names=['MentorFlow.exe','UserRuntime','Installer','SELF-CHECK/source-identity.json',
               'SELF-CHECK/gui-bootstrap-closure.json','SELF-CHECK/package-manifest.json',
               'mentorflow-version.json','_internal/mentorflow-version.json']
        for name in names:
            source=app/name; destination=archive/'App'/name
            if name=='Installer' and not source.exists(): continue
            if not source.resolve().is_relative_to(work.resolve()) or not destination.resolve().is_relative_to(work.resolve()):
                raise ValueError('refresh archive boundary mismatch')
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(source),str(destination))
        for name in ('base.json','target.json','bad.json','asset-map.json','overlays'):
            source=work/'release'/name; destination=archive/name
            if not source.resolve().is_relative_to(work.resolve()) or not destination.resolve().is_relative_to(work.resolve()):
                raise ValueError('release archive boundary mismatch')
            shutil.move(str(source),str(destination))
        if (work/'build-evidence.json').exists():
            (work/'build-evidence.json').rename(archive/'build-evidence.json')
        else:shared.emit(archive/'incomplete-build-evidence.json',old)
    else:
        _,rows=verify(args.payload,args.manifest_sha256)
        app.mkdir(parents=True)
        for name,row in rows.items():
            if name.startswith('UserRuntime/'):
                continue  # rebuilt below; the signed input payload remains untouched
            destination=app/name; destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(args.payload/name,destination)
            if v.sha256_file(destination)!=row['sha256']:raise ValueError('source copy mismatch')
    shutil.copyfile(compiled,app/'MentorFlow.exe')
    shutil.copytree(directory/'native/UserRuntime',app/'UserRuntime')
    (app/'SELF-CHECK/source-identity.json').write_bytes(canonical(identity))
    cache=work/'cache'  # isolated fixture config, not a production/default path
    def version_doc(version,bad=False):
        return dict(schema=v.VERSION_SCHEMA,product='MentorFlow',version=version,channel='stable',
            desktop_notice='当前用户安装与增量更新。',
            **({'uat_recovery_fixture':'exit-before-health-v1'} if bad else {}),
            update=dict(protocol='mentorflow.signed-file-release.v1',enabled=True,
                UPDATE_FEED_URL=args.origin+'/stable.json',metadata_url=args.origin+'/stable.json',
                public_keys={'per-user-uat':base64.b64encode(public).decode()},
                **({} if args.one_click_fixture else {'staging_root':str(cache)})))
    versions = ('1.4.0', '1.4.1', '1.4.2') if args.one_click_fixture else ('1.3.0', '1.3.1', '1.3.2')
    for name in ('mentorflow-version.json','_internal/mentorflow-version.json'):
        (app/name).write_bytes(canonical(version_doc(versions[0])))
    from scripts.prepare_gui_bootstrap_closure import collect_closure
    (app/'SELF-CHECK/gui-bootstrap-closure.json').write_bytes(canonical(collect_closure(app)))
    manifest=v.build_manifest(app); manifest.write_bytes(canonical(json.loads(manifest.read_bytes())))
    verify(app,v.sha256_file(manifest))
    base=json.loads(manifest.read_bytes()); sources={}; evidence=[]
    for epoch,(label,version,bad) in enumerate((('base',versions[0],False),('target',versions[1],False),('bad',versions[2],True)),1):
        overlay=work/'release/overlays'/label; overlay.mkdir(parents=True)
        rows={r['path']:dict(r) for r in base['files']}
        changed={} if label=='base' else {name:canonical(version_doc(version,bad)) for name in ('mentorflow-version.json','_internal/mentorflow-version.json')}
        for name,data in changed.items():rows[name]=dict(path=name,size=len(data),sha256=digest(data))
        doc={**base,'version':version,'files':sorted(rows.values(),key=lambda row:row['path'])}
        changed[v.MANIFEST_RELATIVE.as_posix()]=canonical(doc)
        for name,data in changed.items():
            path=overlay/name; path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
            rows[name]=dict(path=name,size=len(data),sha256=digest(data))
        assets=[]
        for name,row in sorted(rows.items()):
            source=overlay/name if name in changed else app/name
            sources[row['sha256']]=source.relative_to(work/'release').as_posix()
            assets.append({**row,'url':args.origin+'/objects/'+row['sha256']})
        now=datetime.now(timezone.utc)
        signed=dict(schema_version=1,app_id=APP_ID,channel='stable',version=version,metadata_version=epoch,
            issued_at=now.isoformat(),published_at=now.isoformat(),expires_at=(now+timedelta(days=7)).isoformat(),
            key_id='per-user-uat',rollout_percentage=100,rollout_seed='per-user-v1',withdrawn=False,
            min_supported_version=versions[0],min_updater_version='1.0.0',mandatory=False,mandatory_reason='',
            release_notes='隔离健康失败回滚测试。' if bad else '当前用户安装和更新，无需管理员权限。',
            full_release=dict(format='file-set-v1',size=sum(a['size'] for a in assets),assets=assets),
            incremental_assets=list(rows),file_manifest=doc,hash_algorithm='sha256',signature_algorithm='ed25519',
            source_sha=identity['source_sha'],tree_sha=identity['tree_sha'],base_versions=[versions[1]] if bad else [versions[0]])
        raw=canonical(dict(signed=signed,key_id='per-user-uat',signature=base64.b64encode(private.sign(canonical(signed))).decode()))
        (work/'release'/(label+'.json')).write_bytes(raw)
        evidence.append(dict(label=label,version=version,manifest_sha256=digest(canonical(doc)),release_sha256=digest(raw),full_bytes=signed['full_release']['size']))
    (work/'release/asset-map.json').write_bytes(canonical(sources))
    (work/'public-key.json').write_bytes(canonical(dict(key_id='per-user-uat',public_key=base64.b64encode(public).decode())))
    # BASE's package manifest bytes must equal its signed canonical manifest.
    manifest.write_bytes(canonical(base | {'files':sorted(base['files'],key=lambda row:row['path'])}))
    setup=None
    if not args.core_only:
        output=app/'Installer';output.mkdir()
        setup=output/'MentorFlow-1.3.0-PerUser-Setup.exe'
        shared.run([sys.executable,'-B','-m','scripts.build_signed_per_user_installer','--payload',app,
            '--release',work/'release/base.json','--output',output],
            shared.PROJECT,directory/'installer.log',env)
    result=dict(source=identity,app=str(app),installer=str(setup) if setup else None,installer_sha256=v.sha256_file(setup) if setup else None,
        exe_sha256=v.sha256_file(app/'MentorFlow.exe'),runtime_sha256=v.sha256_file(app/'UserRuntime/UserRuntime.exe'),
        releases=evidence,private_key_packaged=False,installed=False)
    shared.emit(work/'build-evidence.json',result)
    print(canonical(result).decode(),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--work',type=Path,required=True);parser.add_argument('--analysis',type=Path,required=True)
    parser.add_argument('--seed-pyz-toc',type=Path,help='Exact accepted desktop PYZ TOC; preserves its existing GUI module closure')
    parser.add_argument('--payload',type=Path,required=True);parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--origin',required=True,help='Explicit build-time feed origin; never a default developer endpoint')
    parser.add_argument('--one-click-fixture',action='store_true',help='Core-only compiled UAT fixture; never a product distribution')
    parser.add_argument('--refresh',action='store_true',help='Retain and replace only this unpublished owned candidate')
    parser.add_argument('--core-only',action='store_true',help='Build packaged engines only; installer is owned by 02')
    parser.add_argument('--source-commit',help='Exact single-parent delivery identity; owned source must match')
    build(parser.parse_args())
