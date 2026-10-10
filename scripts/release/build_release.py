"""Repeatable unsigned MentorFlow release build; never signs/installs/uploads."""
from __future__ import annotations
import argparse, base64, hashlib, importlib.util, json, os, runpy, site, ssl, subprocess, sys, tempfile, time, shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

REPO=Path(__file__).resolve().parents[2]
TOOLS=Path(os.environ.get('MF_BUILD_TOOLS',sys.prefix))
REFERENCE=Path(os.environ.get('MF_REFERENCE_PAYLOAD',str(Path(__file__).parent/'inputs')))
PUBLIC=Path(os.environ.get('MF_PUBLIC_KEY',str(Path(__file__).parent/'inputs/public-key.json')))
NODE=Path(os.environ.get('MF_NODE_DIR',str(Path(shutil.which('node') or 'node').resolve().parent)))
COMPILER=Path(os.environ.get('MF_ISCC',str(Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))/'Inno Setup 7/ISCC.exe')))
# BUG-60: asset URLs in the signed manifest derive from this feed. Clients up to 2.0.9 only
# download from their own (IP) origin, so the manifest stays on the IP origin; 2.0.10+ clients
# map it to the domain first (update_download.update_sources).
LEGACY_ROOT='https://89.45.45.28/mentorflow/updates/app/'
FEED=os.environ.get('MF_FEED',LEGACY_ROOT+'stable/stable.json')
TEMP_ROOT=Path(os.environ.get('MF_TEMP_ROOT',os.environ.get('RUNNER_TEMP',tempfile.gettempdir())))
INPUTS=Path(os.environ.get('MF_RELEASE_INPUTS',str(Path(__file__).parent/'inputs')))

def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def git(repo,*args):
    return subprocess.check_output(['git',*args],cwd=repo,text=True,encoding='utf-8').strip()

def setup(repo,out):
    out=out.resolve()
    if not out.is_relative_to(TEMP_ROOT.resolve()) or out==TEMP_ROOT.resolve():
        raise ValueError('output must be a task directory below MF_TEMP_ROOT / RUNNER_TEMP')
    out.mkdir(parents=True,exist_ok=True)
    scratch=out/'work';scratch.mkdir(exist_ok=True)
    sys.dont_write_bytecode=True;tempfile.tempdir=str(scratch)
    os.environ.update(TEMP=str(scratch),TMP=str(scratch),TMPDIR=str(scratch),PYTHONDONTWRITEBYTECODE='1',
        PYTHONUTF8='1',PYTHONIOENCODING='utf-8',
        NODE_COMPILE_CACHE=str(scratch/'node-cache'))
    os.environ['PATH']=str(NODE)+os.pathsep+os.environ['PATH']
    # The freeze subprocess receives TOOLS through PYTHONPATH before site-packages.
    # Apply that same precedence here so the version check examines what it uses.
    if os.environ.get('MF_BUILD_TOOLS'):
        site.addsitedir(str(TOOLS))
        if str(TOOLS) in sys.path:sys.path.remove(str(TOOLS))
        sys.path.insert(0,str(TOOLS))
    os.environ['PYTHONPATH']=os.pathsep.join(map(str,(repo,TOOLS)))
    sys.path.insert(0,str(repo))
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    os.chdir(repo)
    return scratch

def builder(repo):
    spec=importlib.util.spec_from_file_location('release_builder',repo/'scripts/build_learning_installer.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def authority(payload):
    from PyInstaller.archive.readers import CArchiveReader
    from refactor.desktop.packaging.authority import literal_authority
    values=[literal_authority(CArchiveReader(str(payload/name)).open_embedded_archive('PYZ.pyz').extract('mentorflow_delivery_authority'))
            for name in ('MentorFlow.exe','UserRuntime/UserRuntime.exe')]
    if values[0]!=values[1]:raise ValueError('主程序与更新器公钥身份不一致')
    return values[0]

def resources(ref):
    return dict(runtime=ref/'_internal/refactor/desktop/resources/public-runtime.json',
        ca=ref/'_internal/refactor/desktop/resources/trust/account-api.pem',
        seven=ref/'_internal/refactor/learning/voice/7zr.exe',
        voices=ref/'_internal/refactor/learning/voice/tts/component-catalog.json')

def pinned_reference(ref):
    """Retain only four small public inputs, not the old one-gigabyte payload."""
    root=INPUTS
    manifest=root/'reference.json'
    if not manifest.exists():raise ValueError('missing pinned public build inputs: '+str(manifest))
    record=json.loads(manifest.read_text(encoding='utf-8'))
    paths={name:root/item['filename'] for name,item in record['inputs'].items()}
    for name,path in paths.items():
        if sha(path)!=record['inputs'][name]['sha256']:raise ValueError('保留的2.0.1输入哈希变化：'+name)
    expected=dict(APP_ID=record['app_id'],PUBLIC_KEYS={k:base64.b64decode(v) for k,v in record['public_keys'].items()})
    if (ref/'MentorFlow.exe').exists():
        if authority(ref)!=expected or any(sha(path)!=sha(resources(ref)[name]) for name,path in paths.items()):
            raise ValueError('参照2.0.1与保留输入不一致')
    return expected,paths

def online(feed,ca,keys):
    import httpx
    from refactor.desktop.delivery.update_protocol import verify_release
    # Keep system trust AND the original account CA. Never disable validation.
    tls=ssl.create_default_context();tls.load_verify_locations(cafile=str(ca))
    start=time.monotonic()
    with httpx.Client(verify=tls,trust_env=False,timeout=httpx.Timeout(30,connect=15),follow_redirects=False) as client:
        chunks=[]
        with client.stream('GET',feed) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if time.monotonic()-start>300:raise TimeoutError('读取更新清单超过5分钟')
                chunks.append(chunk)
    return verify_release(b''.join(chunks),keys,current='0.0.0')

def execute(command,repo,log):
    with log.open('w',encoding='utf-8') as stream:
        subprocess.run(list(map(str,command)),cwd=repo,stdout=stream,stderr=subprocess.STDOUT,check=True)

def web(repo,work):
    web=repo/'refactor/learning/web'
    deps=work/'web-deps';deps.mkdir(exist_ok=True)
    for name in ('package.json','package-lock.json'):shutil.copyfile(web/name,deps/name)
    node=NODE/('node.exe' if os.name=='nt' else 'node')
    npm=NODE/'node_modules/npm/bin/npm-cli.js'
    if not npm.is_file():
        npm_bin=Path(shutil.which('npm') or '')
        npm=npm_bin.resolve() if os.name!='nt' else npm_bin.parent/'node_modules/npm/bin/npm-cli.js'
    execute([node,npm,'ci','--no-audit','--no-fund'],deps,work/'npm-ci.log')
    os.environ['MENTORFLOW_WEB_DEPS']=str(deps)
    config=(web/'vite.config.mjs').read_text(encoding='utf-8')
    old='const here = path.dirname(fileURLToPath(import.meta.url))'
    old_out='path.join(here, "dist")'
    if old not in config or old_out not in config:raise ValueError('Vite config structure changed')
    config=config.replace(old,'const here = '+json.dumps(web.as_posix()))
    config=config.replace(old_out,json.dumps((work/'web-dist').as_posix()))
    path=work/'vite.config.mjs';path.write_text(config,encoding='utf-8')
    execute([node,deps/'node_modules/vite/bin/vite.js','build','--config',path],repo,work/'web-build.log')
    return work/'web-dist'

def smoke(payload,work):
    root=work/'smoke';root.mkdir(exist_ok=True)
    env=dict(os.environ)
    for name in ('PYTHONPATH','PYTHONHOME','QT_PLUGIN_PATH','QML2_IMPORT_PATH'):env.pop(name,None)
    windows=Path(os.environ['SystemRoot'])
    env.update(PATH=os.pathsep.join(map(str,(windows/'System32',windows))),
        TEMP=str(root),TMP=str(root),TMPDIR=str(root),QT_QPA_PLATFORM='offscreen')
    start=time.perf_counter()
    process=subprocess.run([payload/'MentorFlow.exe','--packaging-smoke-root',root],cwd=root,
        env=env,capture_output=True,timeout=60)
    result=json.loads((root/'result.json').read_text(encoding='utf-8'))
    result.update(exit_code=process.returncode,seconds=round(time.perf_counter()-start,3),
        stderr=process.stderr.decode('utf-8',errors='replace'))
    if process.returncode or not all(result.get(k) for k in ('backend_ready','source_lists_read','model_prices_read')):
        raise ValueError('冻结程序最小启动失败：'+json.dumps(result,ensure_ascii=False))
    if result['store_type']!='CourseStore' or not Path(result['catalog_data']).resolve().is_relative_to(payload.resolve()):
        raise ValueError('最小启动没有使用包内目录/真实课程库')
    return result

def validate_payload(repo,payload,ref,public,build):
    from refactor.desktop.delivery.update_protocol import Release
    from refactor.desktop.delivery.per_user_install import verify_tree
    build.audit_payload(payload)
    expected,inputs=pinned_reference(ref);actual=authority(payload)
    key_id,key=build.read_key(public)
    if actual!=expected or actual['PUBLIC_KEYS']!={key_id:key}:raise ValueError('2.0.1 公钥/产品身份未沿用')
    hashes={}
    for name,path in inputs.items():
        new=resources(payload)[name]
        hashes[name]=dict(sha256=sha(path),new_sha256=sha(new),bytes=path.stat().st_size)
        if hashes[name]['sha256']!=hashes[name]['new_sha256']:raise ValueError(name+' 与2.0.1哈希不一致')
    data=[]
    for path in sorted((repo/'refactor/learning/course/data').rglob('*')):
        if not path.is_file():continue
        destination=payload/'_internal'/path.relative_to(repo)
        if not destination.is_file() or sha(path)!=sha(destination):raise ValueError('course/data 缺失或不一致：'+str(path))
        data.append(dict(path=str(path.relative_to(repo)),size=path.stat().st_size,sha256=sha(path)))
    if not {'source_lists.json','model-prices.json'}.issubset({Path(r['path']).name for r in data}):raise ValueError('course/data 必需目录缺失')
    return dict(app_id=actual['APP_ID'],public_key_id=key_id,public_key_sha256=hashlib.sha256(key).hexdigest(),
        four_resource_hashes=hashes,course_data=data)

def plans(unsigned,bases):
    from refactor.desktop.delivery.update_protocol import Release,make_plan,canonical
    signed=json.loads(unsigned.read_bytes());manifest=signed['file_manifest']
    rows={r['path']:r for r in manifest['files']}
    for asset in signed['full_release']['assets']:rows.setdefault(asset['path'],asset)
    release=Release(b'',signed,rows,{r['path']:r for r in signed['full_release']['assets']},canonical(manifest))
    result={}
    for version,base in bases.items():
        plan=make_plan(release,base.rows,version)
        if not plan['update'] or plan['update_path']!='FILE_LEVEL_INCREMENTAL':raise ValueError('基线不能走增量：'+version)
        result[version]={**plan,'file_count':len(plan['download_paths']),
                         'unchanged_files':len(rows)-len(plan['download_paths'])}
    return result

def verify_unsigned(payload,unsigned_path):
    from refactor.desktop.delivery.update_protocol import Release,canonical
    from refactor.desktop.delivery.per_user_install import verify_tree
    signed=json.loads(unsigned_path.read_bytes());rows={r['path']:r for r in signed['full_release']['assets']}
    verify_tree(payload,Release(b'',signed,rows,rows,canonical(signed['file_manifest'])))

def commands(repo,out,public):
    def quote(value):return "'"+str(value).replace("'","''")+"'"
    prefix='& '+quote(sys.executable)+' -B '+quote(Path(__file__).resolve())+' tool --repo '+quote(repo)+' --output-dir '+quote(out)+' -- '
    signed=out/'stable/stable.json'
    return dict(sign=prefix+'sign --unsigned '+quote(out/'unsigned-release.json')+' --public-key '+quote(public)+
        " --private-key '<PRIVATE_KEY>' --output "+quote(signed),
        installer=prefix+'installer --payload '+quote(out/'payload')+' --release '+quote(signed)+
        ' --work '+quote(out/'inno')+' --output '+quote(out/'installer')+' --compiler '+quote(COMPILER))

def cleanup(out,work):
    """One native PowerShell attempt; never bypass a refusal with another API."""
    script=out/'cleanup.ps1'
    script.write_text(r'''param([string]$TaskRoot)
$ErrorActionPreference='Stop'
$root=(Resolve-Path -LiteralPath $TaskRoot).Path
foreach($name in @('work')) {
  $target=Join-Path $root $name
  if(Test-Path -LiteralPath $target) {
    $resolved=(Resolve-Path -LiteralPath $target).Path
    if(-not $resolved.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Cleanup outside task directory'}
    if((Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Cleanup target is a reparse point'}
    Remove-Item -LiteralPath $resolved -Recurse -Force
  }
}
''',encoding='utf-8-sig')
    shell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    p=subprocess.run([shell,'-NoProfile','-File',script,'-TaskRoot',out],capture_output=True,text=True)
    return dict(success=p.returncode==0,path=str(work),stderr=p.stderr.strip())

def main():
    if len(sys.argv)>1 and sys.argv[1]=='tool':
        parser=argparse.ArgumentParser();parser.add_argument('--repo',type=Path,default=REPO)
        parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('arguments',nargs=argparse.REMAINDER)
        args=parser.parse_args(sys.argv[2:]);rest=args.arguments
        if rest and rest[0]=='--':rest=rest[1:]
        if not rest or rest[0] not in ('sign','installer'):parser.error('tool 只供架构师运行 sign / installer')
        setup(args.repo.resolve(),args.output_dir)
        sys.argv=[str(args.repo/'scripts/build_learning_installer.py'),*rest]
        runpy.run_path(sys.argv[0],run_name='__main__');return
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('version','baseline','notes'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--valid-days',type=int,required=True);parser.add_argument('--base-versions',nargs='+',required=True)
    parser.add_argument('--mandatory',action='store_true')
    parser.add_argument('--mandatory-reason',choices=('security','protocol','data_compatibility'),default='')
    parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('--repo',type=Path,default=REPO)
    parser.add_argument('--reference-payload',type=Path,default=REFERENCE)
    parser.add_argument('--public-key',type=Path,default=PUBLIC);parser.add_argument('--feed',default=FEED)
    parser.add_argument('--previous-release',type=Path,help='verified local previous stable; no network')
    parser.add_argument('--build-only',action='store_true',help='allow rebuilding current version; never publish')
    parser.add_argument('--allow-dirty',action='store_true',help='local packaging-development verification only; CI never uses this')
    parser.add_argument('--keep-work',action='store_true',help='retain objects until upload finishes')
    parser.add_argument('--base-release',action='append',default=[],help='其它基线：VERSION=已签名清单路径')
    args=parser.parse_args();repo=args.repo.resolve();out=args.output_dir.resolve()
    if not args.allow_dirty and git(repo,'status','--porcelain'):raise ValueError('工位不干净，停止打包')
    baseline=git(repo,'rev-parse',args.baseline)
    if git(repo,'rev-parse','HEAD')!=baseline:raise ValueError('请先同步工位到指定基线')
    if not 1<=args.valid_days<=1100:raise ValueError('有效天数必须符合当前客户端上限1100天')
    work=setup(repo,out);build=builder(repo);ref=args.reference_payload.resolve()
    from scripts.release.check_environment import check
    print('Exact build pins verified: '+str(check(NODE/'node.exe')),flush=True)
    auth,inputs=pinned_reference(ref);key_id,key=build.read_key(args.public_key)
    if auth['PUBLIC_KEYS']!={key_id:key}:raise ValueError('公钥与2.0.1冻结程序不一致')
    recipe=dict(build_only=args.build_only,version=args.version,baseline=baseline,valid_days=args.valid_days,base_versions=args.base_versions,
        notes=args.notes,mandatory=args.mandatory,mandatory_reason=args.mandatory_reason,
        repo=str(repo),reference_payload=str(ref),feed=args.feed,public_key=str(args.public_key.resolve()))
    evidence=out/'build-evidence.json'
    if evidence.exists() and (out/'payload').exists() and (out/'unsigned-release.json').exists():
        old=json.loads(evidence.read_text(encoding='utf-8'))
        if old['recipe']!=recipe:raise ValueError('输出已有不同配方，请指定新目录')
        validate_payload(repo,out/'payload',ref,args.public_key,build)
        verify_unsigned(out/'payload',out/'unsigned-release.json')
        if old.get('cleanup',{}).get('success'):
            outcome=cleanup(out,work)
            if not outcome['success']:print('复核临时目录清理失败：'+json.dumps(outcome,ensure_ascii=False))
        print('已完成的同配方产物通过复核；不重复freeze/release。')
        for line in old['architect_commands'].values():print(line)
        return
    if (out/'payload').exists() or (out/'unsigned-release.json').exists():raise ValueError('已有未完成产物，请使用新的输出目录')
    save(out/'recipe.json',recipe)
    from scripts.server_installer.assemble_payload import verified_release
    live=verified_release(args.previous_release,args.public_key) if args.previous_release else online(args.feed,inputs['ca'],auth['PUBLIC_KEYS'])
    policy=build.release_policy(args.version,live,mandatory=args.mandatory,reason=args.mandatory_reason)
    previous_snapshot=work/'previous-stable.json';previous_snapshot.write_bytes(live.envelope)
    from refactor.desktop.delivery.update_protocol import verify_release,version_key
    if live.signed['app_id']!=auth['APP_ID'] or (version_key(args.version)<version_key(live.version) or (args.version==live.version and not args.build_only)):
        raise ValueError('线上产品身份不一致或目标不是新版本')
    bases={live.version:live}
    for entry in args.base_release:
        version,path=entry.split('=',1)
        value=verify_release(Path(path).read_bytes(),auth['PUBLIC_KEYS'],current='0.0.0')
        if value.version!=version:raise ValueError('基线清单版本不匹配')
        bases[version]=value
    if set(args.base_versions)-set(bases):raise ValueError('缺少某个基线的已验证清单，请传 --base-release')
    bases={v:bases[v] for v in args.base_versions}
    epoch=live.signed['metadata_version']+1
    print(f'线上 {live.version} epoch={epoch-1}；构建 {args.version} epoch={epoch}',flush=True)
    dist=web(repo,work)
    web_evidence=dict(success=True,log_tail=(work/'web-build.log').read_text(encoding='utf-8').splitlines()[-2:])
    freeze_work=work/'freeze'
    build.freeze(argparse.Namespace(version=args.version,work=freeze_work,public_key=args.public_key,
        voice_catalog=inputs['voices'],public_runtime=inputs['runtime'],
        account_ca=inputs['ca'],seven_zip=inputs['seven'],web_dist=dist,
        feed=args.feed,app_id=auth['APP_ID']))
    # One shell moves only this task's completed freeze payload, not a copy.
    built=(freeze_work/'app/MentorFlow').resolve();target=(out/'payload').resolve()
    if not built.is_relative_to(out) or not target.is_relative_to(out):raise ValueError('移动目标超出本任务输出目录')
    built.rename(target)
    package=validate_payload(repo,out/'payload',ref,args.public_key,build)
    ready=smoke(out/'payload',work)
    build.release(argparse.Namespace(payload=out/'payload',public_key=args.public_key,output=work/'publish',
        feed=args.feed,app_id=auth['APP_ID'],epoch=epoch,minimum_version=policy['min_supported_version'],
        mandatory=args.mandatory,mandatory_reason=args.mandatory_reason,previous_release=previous_snapshot,
        base_versions=args.base_versions,valid_days=args.valid_days,notes=args.notes))
    (work/'publish/unsigned-release.json').rename(out/'unsigned-release.json')
    unsigned=json.loads((out/'unsigned-release.json').read_bytes())
    if unsigned['source_sha']!=baseline:raise ValueError('release source_sha 与指定基线不一致')
    if datetime.fromisoformat(unsigned['expires_at'])-datetime.fromisoformat(unsigned['issued_at'])!=timedelta(days=args.valid_days):
        raise ValueError('清单有效天数不一致')
    from refactor.desktop.delivery.update_protocol import Release,canonical
    from refactor.desktop.delivery.per_user_install import verify_tree
    rows={r['path']:r for r in unsigned['full_release']['assets']}
    if any(not r['url'].startswith(LEGACY_ROOT+'objects/') for r in rows.values()):
        raise ValueError('BUG-60：清单下载地址必须在 '+LEGACY_ROOT+'，否则 2.0.9 及更早的客户端无法更新')
    verify_tree(out/'payload',Release(b'',unsigned,rows,rows,canonical(unsigned['file_manifest'])))
    increments=plans(out/'unsigned-release.json',bases)
    result=dict(recipe=recipe,online={k:live.signed[k] for k in ('app_id','version','metadata_version','min_supported_version','expires_at')},
        web=web_evidence,
        epoch=epoch,policy=policy,package=package,smoke=ready,incremental=increments,architect_commands=commands(repo,out,args.public_key),
        full_files=len(rows),full_bytes=sum(p.stat().st_size for p in (out/'payload').rglob('*') if p.is_file()),
        compiler_exists=COMPILER.is_file(),created_at=datetime.now(timezone.utc).isoformat())
    save(evidence,result)
    result['cleanup']=dict(success=False,path=str(work),retained=True) if args.keep_work else cleanup(out,work);save(evidence,result)
    print(json.dumps({k:result[k] for k in ('epoch','full_files','full_bytes','smoke','cleanup')},ensure_ascii=False),flush=True)
    for line in result['architect_commands'].values():print(line)

if __name__=='__main__':main()
