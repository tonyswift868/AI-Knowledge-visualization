"""One resumable UAT release builder. Does not install, elevate or deploy.

Reuses real NSIS/transaction and real desktop entry. BASE is one independent
full copy; TARGET/bad releases are immutable overlays + full signed manifests.
The running production downloader reconstructs complete target trees.
"""
import argparse
import ast
import base64
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import quote

from desktop.update_protocol import canonical,digest,verify_release
from scripts import verify_windows_package as v

PROJECT=Path(__file__).resolve().parents[1]
APP_ID='MentorFlow.UAT.MF14'
UNRELATED={'scripts/measure_installed_login_gate.py','scripts/test_login_form_probe.py'}
BLOCKED_MODULES={'scripts.measure_installed_login_gate','scripts.test_login_form_probe'}


def git(*args):return subprocess.check_output(['git',*args],cwd=PROJECT,text=True).strip()


def source_identity():
    changed=set(git('diff','--name-only').splitlines())|set(git('diff','--cached','--name-only').splitlines())
    untracked=set(git('ls-files','--others','--exclude-standard').splitlines())
    if (changed|untracked)-UNRELATED:raise ValueError('Commit owned source before delivery build; unrelated files remain preserved')
    return dict(source_sha=git('rev-parse','HEAD'),tree_sha=git('rev-parse','HEAD^{tree}'),app_id=APP_ID)


def emit(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(canonical(value))


def run(args,cwd,log,env):
    with Path(log).open('xb') as stream:
        result=subprocess.run([str(a) for a in args],cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,
                              timeout=1800,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('Build failed; preserved log: '+str(log))


def provision(work):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat,PrivateFormat,NoEncryption
    private=work/'keys/uat-ed25519.key';public=work/'public-key.json'
    if private.exists() or public.exists():
        if not (private.is_file() and public.is_file()):raise ValueError('incomplete prior key generation preserved')
        return json.loads(public.read_bytes())
    key=Ed25519PrivateKey.generate();private.parent.mkdir(exist_ok=True)
    with private.open('xb') as stream:stream.write(key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption()))
    doc=dict(key_id='mf14-uat-only',public_key=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode())
    emit(public,doc);return doc


def configure(work,public,identity):
    build=work/'build'/identity['source_sha'][:12];build.mkdir(parents=True,exist_ok=True)
    generated=build/'mentorflow_delivery_authority.py'
    values=dict(APP_ID=APP_ID,WORK_ROOT=str(work),DATA_ROOT=str(work/'data'),
                PUBLIC_KEYS={public['key_id']:base64.b64decode(public['public_key'])},SOURCE_SHA=identity['source_sha'])
    content='"""Compiled UAT public authority; no private signing material."""\n'+''.join(k+' = '+repr(v)+'\n' for k,v in values.items())
    if generated.exists():
        if generated.read_text(encoding='utf-8')!=content:raise ValueError('prior generated authority preserved; use new build root')
    else:
        with generated.open('x',encoding='utf-8',newline='\n') as stream:stream.write(content)
    return build


def build_binaries(args,identity):
    work=args.work;public=provision(work);build=configure(work,public,identity)
    evidence=build/'binaries.json'
    if evidence.exists():
        old=json.loads(evidence.read_bytes())
        if old['source']!=identity:raise ValueError('old binaries belong to different source; preserve and select new work root')
        for row in old['artifacts']:
            if v.sha256_file(Path(row['path']))!=row['sha256']:raise ValueError('built artifact changed')
        return old
    env={**os.environ,'TEMP':str(work),'TMP':str(work),'PYTHONDONTWRITEBYTECODE':'1',
         'PYINSTALLER_CONFIG_DIR':'E:/Codex/Data/cache/pyinstaller',
         'MF_DELIVERY_PROJECT':str(PROJECT),'MF_DELIVERY_BUILD':str(build),'MF_DELIVERY_ANALYSIS':str(args.analysis)}
    artifacts=[]
    # Both privileged/control runtimes are onedir; no arbitrary user TEMP DLLs.
    for name,entry in [('MentorFlowInstallGate','scripts/run_windows_install_gate.py'),('UpdateRuntime','scripts/update_runtime.py')]:
        dist=build/'native'/name
        if dist.exists():raise ValueError('incomplete previous build preserved: '+str(dist))
        command=[sys.executable,'-B','-m','PyInstaller','--onedir','--windowed','--name',name,
            '--paths',str(PROJECT),'--paths',str(build),'--hidden-import','mentorflow_delivery_authority',
            '--distpath',str(build/'native'),'--workpath',str(build/('pyi-'+name)),
            '--specpath',str(build),'--noupx']
        for module in ('torch','PySide6','numpy','pandas','matplotlib','scipy','transformers','docling','pytest','backend','desktop.main'):
            command+=['--exclude-module',module]
        command.append(str(PROJECT/entry))
        run(command,PROJECT,build/(name+'.build.log'),env)
        artifacts.append(dict(path=str(dist/(name+'.exe')),sha256=v.sha256_file(dist/(name+'.exe'))))
    bridge=build/'UpdateBridge.exe'
    run([args.csc,'/nologo','/target:exe','/platform:x64','/reference:System.Web.Extensions.dll',
        '/win32manifest:'+str(PROJECT/'release/update-bridge.manifest'),'/out:'+str(bridge),
        PROJECT/'scripts/native/UpdateBridge.cs',PROJECT/'scripts/native/WindowsIdentityWire.cs'],
        PROJECT,build/'bridge.build.log',env)
    artifacts.append(dict(path=str(bridge),sha256=v.sha256_file(bridge)))
    run([sys.executable,'-B','-m','PyInstaller','--distpath',build/'app','--workpath',build/'pyi-app',
        PROJECT/'release/real-delivery-app.spec'],PROJECT,build/'app.build.log',env)
    # EXE(exclude_binaries=True) without COLLECT deliberately leaves the
    # executable in workpath. Preserve it and explicitly collect that output.
    compiled=build/'pyi-app/real-delivery-app/MentorFlow.exe'
    exe=build/'app/MentorFlow.exe';exe.parent.mkdir(exist_ok=True)
    shutil.copyfile(compiled,exe)
    artifacts.append(dict(path=str(exe),sha256=v.sha256_file(exe)))
    # Verify frozen code did not capture the two protected unrelated scripts.
    from PyInstaller.archive.readers import CArchiveReader
    archive=CArchiveReader(str(exe));pyz=archive.open_embedded_archive('PYZ.pyz')
    if BLOCKED_MODULES & set(pyz.toc):raise ValueError('unrelated probe code captured')
    for module in ('desktop.gui_update_runtime','desktop.update_request','scripts.install_product'):
        if module not in pyz.toc:raise ValueError('missing real delivery module: '+module)
    result=dict(source=identity,artifacts=artifacts,real_entry='desktop_gui_entry.py',private_key_packaged=False)
    emit(evidence,result);return result


def version_doc(version,work,public,origin,*,bad=False):
    return dict(schema=v.VERSION_SCHEMA,product='MentorFlow',version=version,channel='stable',
        desktop_notice=('更新后的程序已就绪：可在此页查看版本并检查下一次更新。' if version!='1.2.0' else '欢迎使用 MentorFlow；在此页管理程序更新。'),
        **({'uat_recovery_fixture':'exit-before-health-v1'} if bad else {}),
        update=dict(protocol='mentorflow.signed-file-release.v1',enabled=True,metadata_url=origin+'/stable.json',
                    public_keys={public['key_id']:public['public_key']},staging_root=str(work/'cache')))


def prepare_base(args,identity):
    work=args.work;result=work/'base-evidence.json'
    if result.exists():
        old=json.loads(result.read_bytes())
        if old['source']!=identity:raise ValueError('base source changed')
        return old
    build_binaries(args,identity);public=provision(work)
    build=configure(work,public,identity)
    source=args.payload;target=work/'release/base/App'
    if target.exists():raise ValueError('incomplete copied BASE preserved: '+str(target))
    from scripts.prepare_transactional_installer import verify
    _,rows=verify(source,args.payload_manifest_sha256)
    target.mkdir(parents=True)
    # Copy and hash from the previously verified manifest, independently.
    for key,row in rows.items():
        destination=target/key;destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/key,destination)
        if v.sha256_file(destination)!=row['sha256']:raise ValueError('source changed during BASE copy: '+key)
    replacements={target/'MentorFlow.exe':build/'app/MentorFlow.exe',
                  target/'MentorFlowInstallGate.exe':build/'native/MentorFlowInstallGate/MentorFlowInstallGate.exe',
                  target/'UpdateBridge.exe':build/'UpdateBridge.exe',
                  target/'SELF-CHECK/verify_windows_package.py':PROJECT/'scripts/verify_windows_package.py'}
    for destination,source_file in replacements.items():shutil.copyfile(source_file,destination)
    for folder,name in [('InstallRuntime','MentorFlowInstallGate'),('UpdateRuntime','UpdateRuntime')]:
        shutil.copytree(build/'native'/name,target/folder)
    # Only newly copied build inputs are replaced; historical bundles untouched.
    public_version=version_doc('1.2.0',work,public,args.origin)
    for relative in ('mentorflow-version.json','_internal/mentorflow-version.json'):
        (target/relative).write_bytes(canonical(public_version))
    (target/'SELF-CHECK/source-identity.json').write_bytes(canonical(identity))
    from scripts.prepare_gui_bootstrap_closure import collect_closure
    (target/'SELF-CHECK/gui-bootstrap-closure.json').write_bytes(canonical(collect_closure(target)))
    manifest=v.build_manifest(target);manifest.write_bytes(canonical(json.loads(manifest.read_bytes())))
    _,verified=verify(target,v.sha256_file(manifest))
    result_doc=dict(source=identity,version='1.2.0',app=str(target),exe_sha256=v.sha256_file(target/'MentorFlow.exe'),
        manifest_sha256=v.sha256_file(manifest),files=len(verified),bytes=sum(r['size'] for r in verified.values()))
    emit(result,result_doc);return result_doc


def releases(args,identity):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    work=args.work;base=prepare_base(args,identity);public=provision(work)
    private=Ed25519PrivateKey.from_private_bytes((work/'keys/uat-ed25519.key').read_bytes())
    app=Path(base['app']);manifest=json.loads((app/v.MANIFEST_RELATIVE).read_bytes())
    release_root=work/'release';index=release_root/'asset-map.json'
    if index.exists():raise ValueError('published local release assets preserved')
    sources={};receipts=[]
    for sequence,(label,version,bad) in enumerate([('target','1.2.1',False),('bad','1.2.2',True)],1):
        overlay=release_root/'overlays'/label;overlay.mkdir(parents=True)
        changed={relative:canonical(version_doc(version,work,public,args.origin,bad=bad))
                 for relative in ('mentorflow-version.json','_internal/mentorflow-version.json')}
        rows={row['path']:dict(row) for row in manifest['files']}
        for path,data in changed.items():rows[path]=dict(path=path,size=len(data),sha256=digest(data))
        target={**manifest,'version':version,'files':sorted(rows.values(),key=lambda r:r['path'])}
        changed[v.MANIFEST_RELATIVE.as_posix()]=canonical(target)
        for path,data in changed.items():
            dest=overlay/path;dest.parent.mkdir(parents=True,exist_ok=True)
            with dest.open('xb') as stream:stream.write(data)
            rows[path]=dict(path=path,size=len(data),sha256=digest(data))
        assets=[]
        for path,row in sorted(rows.items()):
            source=overlay/path if path in changed else app/path
            relative=source.relative_to(release_root).as_posix()
            sources[row['sha256']]=relative
            assets.append({**row,'url':args.origin+'/objects/'+row['sha256']})
        now=datetime.now(timezone.utc)
        signed=dict(schema_version=1,app_id=APP_ID,channel='stable',version=version,metadata_version=sequence,
            issued_at=now.isoformat(),published_at=now.isoformat(),expires_at=(now+timedelta(days=7)).isoformat(),
            key_id=public['key_id'],rollout_percentage=100,rollout_seed='mf14-v1',withdrawn=False,
            min_supported_version='1.2.0',min_updater_version='1.0.0',mandatory=False,mandatory_reason='',
            release_notes='自动恢复验证：受控坏版，仅隔离 UAT。' if bad else '关于页面新增更新完成说明；版本更新至 1.2.1。',
            full_release=dict(format='file-set-v1',size=sum(a['size'] for a in assets),assets=assets),
            incremental_assets=[a['path'] for a in assets],file_manifest=target,hash_algorithm='sha256',
            signature_algorithm='ed25519',source_sha=identity['source_sha'],tree_sha=identity['tree_sha'],
            base_versions=['1.2.1'] if bad else ['1.2.0'])
        envelope=canonical(dict(signed=signed,key_id=public['key_id'],signature=base64.b64encode(private.sign(canonical(signed))).decode()))
        # Shared verifier uses compiled namespace; builder supplies an explicit
        # local namespace adapter only during offline artifact validation.
        from unittest.mock import patch
        import desktop.update_protocol as protocol
        with patch.object(protocol,'APP_ID',APP_ID):
            verify_release(envelope,{public['key_id']:base64.b64decode(public['public_key'])},current=signed['base_versions'][0])
        with (release_root/(label+'.json')).open('xb') as stream:stream.write(envelope)
        receipts.append(dict(label=label,version=version,manifest_sha256=digest(canonical(target)),
            full_target_bytes=signed['full_release']['size'],changed_payload_bytes=sum(len(b) for b in changed.values()),
            release_sha256=digest(envelope)))
    emit(index,sources);emit(work/'releases-evidence.json',dict(source=identity,releases=receipts))


def installer(args,identity):
    base=prepare_base(args,identity);app=Path(base['app']);output=app/'Installer'
    run([os.environ['SystemRoot']+'/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-NonInteractive','-File',
        PROJECT/'scripts/build_windows_installer.ps1','-ProtectedInstall','-PayloadRoot',app,'-OutputRoot',output,
        '-PythonPath',sys.executable,'-BuildScratch',args.work/'build/nsis','-SourceSha',identity['source_sha'],
        '-SourceTree',identity['tree_sha'],'-UatAppId',APP_ID,'-UatWorkRoot',args.work,
        '-UatDefaultInstallDir',args.install_root],PROJECT,args.work/'build/installer.build.log',
        {**os.environ,'TEMP':str(args.work),'TMP':str(args.work),'PYTHONDONTWRITEBYTECODE':'1'})
    files=list(output.glob('*.exe'))
    if len(files)!=1:raise ValueError('exactly one final installer expected')
    emit(args.work/'installer-evidence.json',dict(source=identity,path=str(files[0]),sha256=v.sha256_file(files[0]),
        authenticode='UNSIGNED',installed=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['binaries','base','releases','installer'])
    parser.add_argument('--work',type=Path,default=Path('D:/MF14W'))
    parser.add_argument('--install-root',type=Path,default=Path('D:/MF14I'))
    parser.add_argument('--payload',type=Path,default=Path('D:/MF13B/App'))
    parser.add_argument('--payload-manifest-sha256',required=True)
    parser.add_argument('--analysis',type=Path,required=True)
    parser.add_argument('--origin',default='http://127.0.0.1:48147')
    parser.add_argument('--csc',type=Path,default=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe'))
    args=parser.parse_args();args.work=args.work.absolute()
    if args.work!=Path('D:/MF14W') or args.install_root!=Path('D:/MF14I'):
        raise ValueError('This explicit UAT delivery profile is bound to approved isolated roots')
    if args.origin!='http://127.0.0.1:48147':raise ValueError('compiled local UAT origin is fixed for this delivery')
    identity=source_identity()
    {'binaries':build_binaries,'base':prepare_base,'releases':releases,'installer':installer}[args.phase](args,identity)
    print(json.dumps(dict(phase=args.phase,status='BUILT_NOT_INSTALLED',source=identity)))


if __name__=='__main__':main()
