"""Build one sealed fixed-authority UAT seed runtime/native launcher; no UAC."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from scripts.install_receipt_state import inspect_root
from scripts.qualify_uat_relocation import sha


def main():
    repo=Path(__file__).resolve().parents[1]
    work=Path('E:/Codex/Temp/MF13-build')
    if work.exists():raise RuntimeError('existing seed build preserved')
    work.mkdir(parents=True)
    state=inspect_root('D:/MFUAC')
    assert state['state']=='ACL_READY_NO_BASE'
    authority=dict(install_root='D:\\MFUAC',source_root='D:\\MF13B\\App',root_identity=state['root_identity'],
        bootstrap_sha256=state['bootstrap_sha256'],manifest_sha256='31e4f83aa212ae122cf1da375327d88208441c31da5aea95cae8175eec94a5a5',
        app_exe_sha256='971ac27e1ab108cd66c7a9d1c0e9c93280327c6c5a1726340cf44ab6579b4894',
        source_sha='db95f5ce09e446f74a7b63415956035ab2f59398',tree_sha='23edc25264d358c8e0313777b4cba396127dc844',version='1.1.0')
    (work/'authority.json').write_text(json.dumps(authority,indent=2))
    (work/'mentorflow_seed_authority.py').write_text('AUTHORITY = '+repr(authority)+'\nREQUEST_PATH = '+repr(str(work/'seed-request.json'))+'\n',encoding='utf-8')
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',TEMP=str(work),TMP=str(work),PYINSTALLER_CONFIG_DIR='E:/Codex/Data/cache/pyinstaller')
    command=[sys.executable,'-B','-m','PyInstaller','--noconfirm','--onedir','--console','--name','SeedRuntime',
        '--paths',str(repo),'--paths',str(work),'--hidden-import','mentorflow_seed_authority',
        '--distpath',str(work/'dist'),'--workpath',str(work/'build'),'--specpath',str(work)]
    for name in ('PySide6','torch','numpy','pandas','backend','desktop.main','scripts.windows_install_shortcut','scripts.windows_installed_launch','scripts.windows_install_progress'):
        command+=['--exclude-module',name]
    command+=[str(repo/'scripts/uat_seed_entry.py')]
    with (work/'build.log').open('x',encoding='utf-8') as log:
        subprocess.run(command,env=env,cwd=repo,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=480)
    runtime=work/'dist/SeedRuntime'
    zip_path=work/'seed-payload.zip'
    with zipfile.ZipFile(zip_path,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(runtime.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(runtime).as_posix())
    pins='static class SeedPins { public const ulong Volume = %dUL; public const ulong RootId = %dUL; public const string BootstrapSha = "%s"; public const string PayloadSha = "%s"; }'%(
        state['root_identity'][0],state['root_identity'][1],state['bootstrap_sha256'],sha(zip_path))
    (work/'SeedPins.cs').write_text(pins)
    # App remains asInvoker. Only the explicitly launched native seed process
    # receives runas; its sealed runtime is extracted into protected staging.
    manifest=Path('D:/CodexScratch/MentorFlow/MF-E107-UAT-PROTECTED-ROOT-BOOTSTRAP-20260907-10/bootstrap.manifest')
    exe=work/'MentorFlow UAT BASE Seed.exe'
    subprocess.run(['C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe','/nologo','/target:exe','/optimize+',
        '/reference:System.Web.Extensions.dll','/reference:System.IO.Compression.dll',
        '/win32manifest:'+str(manifest),'/resource:'+str(zip_path)+',seed-payload.zip','/out:'+str(exe),
        str(repo/'scripts/native/UatSeedLauncher.cs'),str(work/'SeedPins.cs')],check=True,cwd=repo)
    result=dict(native_exe=str(exe),native_sha256=sha(exe),payload_sha256=sha(zip_path),runtime_exe_sha256=sha(runtime/'SeedRuntime.exe'),
        authority=authority,build_count=1,UAC=0)
    (work/'build-identity.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
