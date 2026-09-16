"""Remote branch selection for the existing source desktop entry, outside QA approval."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

from .targets import ROOT
from backend.app.plain_file_copy import assert_no_reparse_ancestors

CACHE = Path('D:/CodexScratch/MentorFlow/workbench-launch')
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class LaunchError(RuntimeError):
    pass


class BranchLauncher:
    def __init__(self, root=ROOT, cache=CACHE, python=None, profile_base=None, test_only=False):
        self.root, self.cache = Path(root), Path(cache)
        self.python = Path(python or sys.executable)
        self.profile_base = Path(profile_base or 'E:/MentorFlowWorkbenchData')
        self.test_only = test_only

    def git(self, *args):
        try:
            result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True,
                timeout=120, creationflags=FLAGS, env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
        except (OSError, subprocess.TimeoutExpired):
            raise LaunchError('Git 查询超时或无法执行；请检查网络和本机 Git。') from None
        if result.returncode:
            raise LaunchError('Git 查询失败；请检查网络和 origin 访问权限。')
        return result.stdout.decode('utf-8').strip()

    def resolve(self, channel):
        if channel not in ('development', 'release'):
            raise LaunchError('未知版本入口')
        lines = self.git('ls-remote', '--symref', 'origin', 'HEAD',
                         'refs/heads/control/main3-current').splitlines()
        refs, default = {}, None
        for line in lines:
            value, name = line.split('\t', 1)
            if value.startswith('ref: '): default = value[5:]
            else: refs[name] = value
        branch = 'refs/heads/control/main3-current' if channel == 'development' else default
        sha = refs.get(branch if channel == 'development' else 'HEAD')
        if not branch or not branch.startswith('refs/heads/') or not re.fullmatch('[0-9a-f]{40}', sha or ''):
            raise LaunchError('远端没有可解析的版本；不会使用其他分支代替。')
        return dict(channel=channel, branch=branch[11:], sha=sha)

    def safe(self, path):
        path = Path(path).absolute()
        path.relative_to(self.cache.absolute())
        assert_no_reparse_ancestors(path, Path(path.anchor))
        return path

    def run(self, command, cwd, log, env):
        with log.open('ab') as output:
            try:
                result = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                    stdout=output, stderr=subprocess.STDOUT, timeout=1800, creationflags=FLAGS)
            except (OSError, subprocess.TimeoutExpired):
                raise LaunchError(f'准备命令无法执行或超时。诊断日志：{log}') from None
        if result.returncode:
            raise LaunchError(f'准备失败（退出码 {result.returncode}）。诊断日志：{log}')

    def source_matches(self, source, sha):
        try:
            for row in self.git('-c', 'core.quotepath=false', 'ls-tree', '-r', sha).splitlines():
                meta, name = row.split('\t', 1)
                mode, kind, oid = meta.split()
                if mode not in ('100644', '100755') or kind != 'blob': return False
                path = self.safe(source / name)
                raw = path.read_bytes()
                if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != oid:
                    return False
            return True
        except (OSError, ValueError):
            return False

    def prepare(self, selected, progress=lambda text: None):
        sha = selected['sha']
        progress(f"获取 {selected['branch']} · {sha[:12]}")
        # Fetch objects only, never move a branch, worktree or default-branch ref.
        self.git('fetch', '--no-tags', '--no-write-fetch-head', 'origin', sha)
        base = self.safe(self.cache / 'sources' / sha); base.mkdir(parents=True, exist_ok=True)
        for receipt in sorted(base.glob('*/ready.json'), reverse=True):
            try:
                data = json.loads(self.safe(receipt).read_text())
                source = receipt.parent / 'source'
                valid = data['sha'] == sha and self.source_matches(source, sha)
                valid = valid and 'frontend/dist/index.html' in data['frontend']
                valid = valid and all(p.startswith('frontend/dist/') and '..' not in Path(p).parts
                                      for p in data['frontend'])
                valid = valid and all(hashlib.sha256(self.safe(source / p).read_bytes()).hexdigest() == h
                                      for p, h in data['frontend'].items())
                if valid and data['frontend']:
                    self.prepare_lifecycle(source, progress)
                    progress('源码和前端缓存校验通过'); return source
            except (OSError, ValueError, KeyError): pass
        work = self.safe(base / uuid.uuid4().hex); work.mkdir()
        archive, source = work / 'source.zip', work / 'source'
        self.git('-c', 'core.autocrlf=false', 'archive', '--format=zip', '--output='+str(archive), sha)
        with zipfile.ZipFile(archive) as package:
            for item in package.infolist():
                parts = Path(item.filename)
                if parts.is_absolute() or '..' in parts.parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise LaunchError('源码归档包含不安全路径')
            package.extractall(source)
        if not self.source_matches(source, sha): raise LaunchError('源码缓存与选择的 SHA 不一致')
        env = self.environment(work / 'build-profile')
        log = work / 'prepare.log'
        npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
        if not npm: raise LaunchError('未找到现有 Node/npm，请安装项目所需运行时后重试。')
        progress('准备该版本的前端依赖（npm ci）…')
        env['npm_config_cache'] = str(self.cache / 'npm-cache')
        self.run([npm, 'ci', '--no-audit', '--no-fund'], source / 'frontend', log, env)
        progress('构建该版本的前端（npm run build）…')
        self.run([npm, 'run', 'build'], source / 'frontend', log, env)
        files = {p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in (source / 'frontend/dist').rglob('*') if p.is_file()}
        if 'frontend/dist/index.html' not in files: raise LaunchError(f'前端构建未产生入口：{log}')
        (work / 'ready.json').write_text(json.dumps(dict(sha=sha, frontend=files)), encoding='utf-8')
        self.prepare_lifecycle(source, progress)
        return source

    def prepare_lifecycle(self, source, progress):
        script = source / 'scripts/render_account_lifecycle_ui.py'
        if not script.is_file(): return  # Older branches retain their own UI.
        output = source / 'artifacts/account-lifecycle-ui'
        inputs = [source/'tests/theme/ResetPasswordThemeFixture.java',
                  *sorted((source/'deploy/enterprise/themes/mentorflow-desktop-v1/login').rglob('*'))]
        expected = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in inputs if p.is_file()}
        proof = output / 'workbench-output.json'
        try:
            data = json.loads(self.safe(proof).read_text())
            if (data['inputs'] == expected and
                {'lifecycle.html', 'resources/js/account-lifecycle-native.js'} <= data['outputs'].keys() and
                all(not Path(p).is_absolute() and '..' not in Path(p).parts for p in data['outputs']) and
                all(hashlib.sha256(self.safe(output/p).read_bytes()).hexdigest()==h
                    for p,h in data['outputs'].items())): return
        except (OSError, ValueError, KeyError): pass
        tools = Path('E:/Codex/Temp/mf-e109-sec04-tools-20260912')
        keycloak = Path(os.environ.get('MENTORFLOW_WORKBENCH_KEYCLOAK', str(tools/'keycloak-26.7.3')))
        jdk = Path(os.environ.get('MENTORFLOW_WORKBENCH_JDK', str(tools/'jdk-21.0.12.1+1')))
        if not (keycloak/'lib/lib/main/org.keycloak.keycloak-themes-26.7.3.jar').is_file() or not all(
                (jdk/'bin'/name).is_file() for name in ('java.exe','javac.exe')):
            raise LaunchError('账号页面构建依赖缺失：需要现有 Keycloak 26.7.3 与 JDK；'
                              '设置 MENTORFLOW_WORKBENCH_KEYCLOAK / MENTORFLOW_WORKBENCH_JDK 后重试。')
        progress('准备该版本现有账号页面（Keycloak renderer）…')
        self.run([self.python,'-B',script,'--keycloak',keycloak,'--jdk',jdk,'--output',output],
                 source, source.parent/'prepare.log', self.environment(source.parent/'build-profile'))
        outputs = {p.relative_to(output).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in output.rglob('*') if p.is_file() and p != proof}
        if 'lifecycle.html' not in outputs: raise LaunchError('账号页面构建未产生 lifecycle.html')
        proof.write_text(json.dumps(dict(inputs=expected, outputs=outputs)), encoding='utf-8')

    def environment(self, profile):
        profile = Path(profile).absolute()
        if not profile.is_relative_to(self.cache.absolute()) and not profile.is_relative_to(self.profile_base.absolute()):
            raise LaunchError('数据目录不属于本工作台')
        assert_no_reparse_ancestors(profile, Path(profile.anchor))
        profile.mkdir(parents=True, exist_ok=True)
        env = {k:v for k,v in os.environ.items() if not k.startswith(('MENTORFLOW_', 'PYTHON', 'QT'))}
        # Public endpoints are normal-launch configuration, never a TEST lease.
        import certifi
        config_root = Path(__file__).parent
        public = json.loads((config_root / 'public-runtime.json').read_text(encoding='utf-8'))
        env.update(public)
        trust = self.safe(self.cache / 'trust'); trust.mkdir(parents=True, exist_ok=True)
        bundle = trust / 'account-api-ca.pem'
        bundle.write_bytes(Path(certifi.where()).read_bytes() + b'\n' +
                           (config_root / 'trust/account-api.pem').read_bytes())
        env['SSL_CERT_FILE'] = str(bundle)
        for key in ('MENTORFLOW_DESKTOP_OIDC_ISSUER', 'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID',
                    'MENTORFLOW_DESKTOP_OIDC_AUDIENCE', 'MENTORFLOW_ENTERPRISE_API_BASE_URL',
                    'MENTORFLOW_DESKTOP_ORGANIZATION_ID', 'MENTORFLOW_DESKTOP_WORKSPACE_ID',
                    'MENTORFLOW_DESKTOP_IDENTITY_ISSUER'):
            if key in os.environ: env[key] = os.environ[key]
        # Never inherit issuer cutover, synthetic auth or expired runtime flags.
        for name in ('temp', 'local', 'roaming', 'data', 'vault'):
            (profile / name).mkdir(exist_ok=True)
        env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', TEMP=str(profile/'temp'), TMP=str(profile/'temp'),
            LOCALAPPDATA=str(profile/'local'), APPDATA=str(profile/'roaming'),
            MENTORFLOW_USER_DATA_ROOT=str(profile/'data'), MENTORFLOW_ACCOUNT_VAULT_ROOT=str(profile/'vault'),
            MENTORFLOW_DATA_DIR=str(profile/'data/data'), MENTORFLOW_FIRST_START_ACTION='defaults',
            MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE='1')
        if self.test_only: env['MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY'] = '1'
        return env

    def launch(self, channel, progress=lambda text: None, *, smoke_seconds=0):
        selected = self.resolve(channel)
        progress(f"已选择 {selected['branch']} · {selected['sha']}（本次启动固定此版本）")
        source = self.prepare(selected, progress)
        profile = self.profile_base / channel; env = self.environment(profile)
        run = self.safe(self.cache / 'runs' / uuid.uuid4().hex); run.mkdir(parents=True)
        log, identity = run / 'desktop.log', run / 'identity.json'
        env['MENTORFLOW_STARTUP_LOG'] = str(run / 'startup.jsonl')
        if smoke_seconds: env['MENTORFLOW_DESKTOP_SMOKE_SECONDS'] = str(smoke_seconds)
        progress('启动所选源码版本…')
        try:
            with log.open('wb') as output:
                process = subprocess.Popen([str(self.python), '-B', str(Path(__file__).with_name('source_entry.py')),
                    str(source), str(profile), str(identity), json.dumps(selected)], cwd=source, env=env,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=FLAGS)
        except OSError:
            raise LaunchError(f'Python 无法启动，请检查工作台运行时。诊断日志：{log}') from None
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LaunchError(f'启动退出（{process.returncode}）；未回退其他版本。日志：{log}')
            if identity.exists():
                actual = json.loads(identity.read_text())
                if (process.pid not in (actual.get('pid'), actual.get('parent_pid')) or
                    actual.get('sha') != selected['sha'] or
                    Path(actual.get('desktop_module', '')) != source/'desktop/main.py'):
                    raise LaunchError(f'运行身份校验失败：{identity}')
                return {**selected, 'pid': actual['pid'], 'launcher_pid': process.pid,
                        'source': str(source), 'profile': str(profile),
                        'log': str(log), 'identity': str(identity), 'mode': 'SOURCE_DESKTOP_MAIN'}
            time.sleep(.2)
        raise LaunchError(f'启动未在 60 秒内确认窗口；进程 {process.pid}，日志：{log}')
