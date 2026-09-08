"""Build normal Windows install UX; no installation, account I/O or UAC.

python -B -m scripts.build_normal_desktop_delivery --payload <verified-existing-payload>
  --manifest-sha256 <exact-pin> --analysis <existing-Analysis-00.toc>
Uses one disposable build scratch and emits a relocation-safe candidate.
"""
import argparse,base64,json,os,shutil,sys
from pathlib import Path
from scripts import build_real_update_delivery as build
from scripts import verify_windows_package as v
from scripts.prepare_transactional_installer import verify
from desktop.update_protocol import canonical

def configure(work,public,identity):
    directory=work/'build'/identity['source_sha'][:12];directory.mkdir(parents=True,exist_ok=True)
    code=('"""Build provisioned preview identity; runtime paths come from this Windows user."""\n'
          'import os\nfrom pathlib import Path\n'
          "APP_ID='MentorFlow.UAT.Current'\n"
          "WORK_ROOT=str(Path(os.environ['LOCALAPPDATA'])/'MentorFlowPreview')\n"
          "DATA_ROOT=str(Path(WORK_ROOT)/'data')\nPUBLIC_KEYS={}\n"
          f"SOURCE_SHA={identity['source_sha']!r}\n")
    path=directory/'mentorflow_delivery_authority.py'
    if path.exists() and path.read_text(encoding='utf-8')!=code:raise ValueError('build authority conflict')
    if not path.exists():path.write_text(code,encoding='utf-8')
    return directory

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload',type=Path,required=True);parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--analysis',type=Path,required=True)
    args=parser.parse_args();args.work=Path('D:/MentorFlow-BuildScratch')
    args.csc=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    args.work.mkdir(exist_ok=True)
    build.APP_ID='MentorFlow.UAT.Current';build.configure=configure
    build.provision=lambda work:{} # This installer candidate does not provision a live update feed/key.
    identity=build.source_identity();build.build_binaries(args,identity)
    directory=configure(args.work,{},identity);app=args.work/'Candidate'
    if app.exists():
        # A bounded repair may refresh our unpublished candidate, not a final
        # distribution. Verify it first, retain its identity and evidence.
        old_identity=json.loads((app/'SELF-CHECK/source-identity.json').read_bytes())
        build.git('merge-base','--is-ancestor',old_identity['source_sha'],identity['source_sha'])
        verify(app,v.sha256_file(app/v.MANIFEST_RELATIVE))
        prior=directory/'prior-candidate';prior.mkdir()
        for name in ('source-identity.json','package-manifest.json'):
            shutil.copyfile(app/'SELF-CHECK'/name,prior/name)
        if (app/'Installer').exists():
            if (app/'Installer').resolve().parent!=app.resolve():raise ValueError('installer scratch alias')
            shutil.rmtree(app/'Installer')
    else:
        _,rows=verify(args.payload,args.manifest_sha256)
        app.mkdir()
        for key,row in rows.items():
            dest=app/key;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(args.payload/key,dest)
            if v.sha256_file(dest)!=row['sha256']:raise ValueError('source changed during copy')
    shutil.copyfile(directory/'app/MentorFlow.exe',app/'MentorFlow.exe')
    shutil.copyfile(directory/'native/MentorFlowInstallGate/MentorFlowInstallGate.exe',app/'MentorFlowInstallGate.exe')
    shutil.copyfile(directory/'UpdateBridge.exe',app/'UpdateBridge.exe')
    # Replace only this newly-created scratch candidate, never historical payloads.
    for name,source in [('InstallRuntime','MentorFlowInstallGate'),('UpdateRuntime','UpdateRuntime')]:
        target=app/name
        if target.exists():
            if target.resolve().parent!=app.resolve():raise ValueError('runtime replacement escaped scratch')
            shutil.rmtree(target)
        shutil.copytree(directory/'native'/source,target)
    doc=dict(schema=v.VERSION_SCHEMA,product='MentorFlow',version='1.2.3',channel='stable',
             desktop_notice='您可以在此查看软件版本、检查更新。',update=dict(enabled=False))
    for relative in ('mentorflow-version.json','_internal/mentorflow-version.json'):(app/relative).write_bytes(canonical(doc))
    (app/'SELF-CHECK/source-identity.json').write_bytes(canonical(identity))
    shutil.copyfile(build.PROJECT/'scripts/verify_windows_package.py',app/'SELF-CHECK/verify_windows_package.py')
    from scripts.prepare_gui_bootstrap_closure import collect_closure
    (app/'SELF-CHECK/gui-bootstrap-closure.json').write_bytes(canonical(collect_closure(app)))
    manifest=v.build_manifest(app);manifest.write_bytes(canonical(json.loads(manifest.read_bytes())))
    _,rows=verify(app,v.sha256_file(manifest))
    output=app/'Installer'
    build.run([os.environ['SystemRoot']+'/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-NonInteractive','-File',
               build.PROJECT/'scripts/build_windows_installer.ps1','-ProtectedInstall','-PayloadRoot',app,'-OutputRoot',output,
               '-PythonPath',sys.executable,'-BuildScratch',args.work/'nsis','-SourceSha',identity['source_sha'],
               '-SourceTree',identity['tree_sha'],'-UatAppId',identity['app_id'],'-UatWorkRoot',args.work],build.PROJECT,
               directory/'installer.log',{**os.environ,'PSModulePath':os.environ['SystemRoot']+'/System32/WindowsPowerShell/v1.0/Modules'})
    setup=output/'MentorFlow-1.2.3-win-x64-Setup.exe'
    build.emit(directory/'candidate-evidence.json',dict(source=identity,app=str(app),version=doc['version'],
        exe_sha256=v.sha256_file(app/'MentorFlow.exe'),manifest_sha256=v.sha256_file(manifest),
        installer_sha256=v.sha256_file(setup),payload_bytes=sum(row['size'] for row in rows.values()),
        public_feed_configured=False,installed=False))
    print('CANDIDATE_BUILT_NOT_INSTALLED',flush=True)

if __name__=='__main__':main()
