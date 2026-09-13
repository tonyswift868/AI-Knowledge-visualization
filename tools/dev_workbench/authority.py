"""Read a single Git snapshot and approved receipts; never write governance."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

from .targets import ROOT, LIMIT, Refused, TrustedPaths, json_bytes, read_checked, sha256, validate_manifest

BOARD = 'docs/governance/ACTIVE_BOARD.json'
QUEUE = 'docs/governance/DEPENDENCY_QUEUE.json'
CURRENT = 'docs/project/CURRENT_STATE.md'
PLAN = 'docs/exec-plans/active/MAIN3.md'
HANDOFF = 'docs/handoff/WEB_SESSION_LATEST.md'
TARGET = 'docs/governance/ACTIVE_TEST_TARGET.json'
SLOTS = ('A', '01', '02', '03', '04')
WORKER_STATES = {'RUNNING', 'SILENT', 'BLOCKED', 'USER_ACTION_REQUIRED', 'PASS_STAGE',
                 'WAITING_FOR_ARTIFACT', 'NOT_RUN', 'LOCKED'}
FLOW_STATES = {'DONE': 'PASS', 'PASS': 'PASS', 'RUNNING': 'RUNNING', 'BLOCKED': 'BLOCKED',
               'LOCKED': 'LOCKED', 'NOT_RUN': 'NOT_RUN', 'NOT_DISPATCHED': 'NOT_RUN'}
LABELS = {
    'BATCH02_EXACT_INTEGRATION': 'Batch02 源码集成',
    'GATE_A_SCHEMA_0036_UNBLOCK': '0036 schema 解阻',
    'GATE_A_AUTH_LIVE_ACTIVATION': 'Gate A · 原生登录部署验收',
    'GATE_B_ACCOUNT_SECURITY': 'Gate B · 账户与邮件验收',
    'GATE_C_UPDATE_FINAL_CANDIDATE': 'Gate C · 最终更新候选',
    'FINAL_QA_USER_ACCEPTANCE': '最终用户验收',
    'INTERVIEW_ACCEPTANCE_BUNDLE': '交付与验收材料',
    'MAIN3_PROMOTION': '正式 main3',
    'REAL_SMTP_TRANSPORT': '真实 SMTP 传输', 'REAL_QQ_DELIVERY': '真实 QQ 投递',
}


class RepoSource:
    def __init__(self, root=ROOT, ref='refs/heads/integration/main3'):
        self.root = Path(root)
        if ref != 'refs/heads/integration/main3' and not re.fullmatch('[0-9a-f]{40}', ref):
            raise Refused('INVALID_AUTHORITY_REF')
        self.sha = self.git('rev-parse', '--verify', ref + '^{commit}')
        self.tree = self.tree_for(self.sha)
        self.timestamp = self.git('show', '-s', '--format=%cI', self.sha)

    def git(self, *args):
        try:
            result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True,
                                    timeout=4, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'},
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode: raise Refused('REPO_STATE_UNAVAILABLE')
            return result.stdout.decode('utf-8').strip()
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            raise Refused('REPO_STATE_UNAVAILABLE') from None

    def tree_for(self, sha):
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise Refused('SOURCE_IDENTITY_MISMATCH')
        return self.git('rev-parse', '--verify', sha + '^{tree}')

    def read(self, path):
        if not re.fullmatch(r'docs/[A-Za-z0-9_./-]+', path) or '..' in path.split('/'):
            raise Refused('UNTRUSTED_RECEIPT_PATH')
        try:
            size = int(self.git('cat-file', '-s', f'{self.sha}:{path}'))
        except Refused:
            return None
        if size > LIMIT: raise Refused('INPUT_TOO_LARGE')
        # Git blob bytes are exact; do not normalize whitespace before hashing.
        result = subprocess.run(['git', '-C', str(self.root), 'show', f'{self.sha}:{path}'],
            capture_output=True, timeout=4, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode: raise Refused('REPO_STATE_UNAVAILABLE')
        return result.stdout

    def assert_current(self):
        # A pinned snapshot is evidence, but cannot authorize after supersession.
        if self.git('rev-parse', '--verify', 'refs/heads/integration/main3^{commit}') != self.sha:
            raise Refused('TARGET_CHANGED')

    def qualify(self, manifest):
        """Bind an exact build to a publication-only descendant on integration.

        The publication SHA comes from Git, never a self-referential field inside
        the commit being created. Runtime changes require a new candidate build.
        """
        validate_manifest(manifest)
        self.assert_current()
        build = manifest['source_sha']
        if self.tree_for(build) != manifest['source_tree']:
            raise Refused('SOURCE_IDENTITY_MISMATCH')
        if self.git('merge-base', build, self.sha) != build:
            raise Refused('SOURCE_NOT_INTEGRATED')
        changed = self.git('diff', '--name-only', '--no-renames', build, self.sha, '--').splitlines()
        publication_files = {CURRENT, PLAN, HANDOFF}
        if any(not (path.startswith('docs/governance/') or path in publication_files) for path in changed):
            raise Refused('BUILD_SOURCE_SUPERSEDED')
        return dict(build_source_sha=build, build_source_tree=manifest['source_tree'],
                    governance_publication_sha=self.sha, artifact_sha256=manifest['artifact_sha256'],
                    build_id=manifest['build_id'], test_id=manifest['test_id'],
                    identity_evidence_sha256=manifest['identity_evidence_sha256'])


def public_text(value, secrets=()):
    if not isinstance(value, str) or not value: return 'UNKNOWN'
    for secret in secrets:
        if secret: value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'(?i)\b(password|token|api[_ -]?key|credential|secret)\s*(?:=|:(?!//))\s*\S+',
                   r'\1=SECURE_REFERENCE_ONLY', value)
    return ''.join(c for c in value if c in '\n\t' or ord(c) >= 32)[:1500]


def secret_values(value):
    values = []
    if isinstance(value, dict):
        for key, child in value.items():
            if re.search(r'password|secret|token|credential', key, re.I) and key.lower() != 'secret_ref':
                if isinstance(child, str): values.append(child)
            values.extend(secret_values(child))
    elif isinstance(value, list):
        for child in value: values.extend(secret_values(child))
    return values


def receipt_to_manifest(receipt, test_id, *, created_at=None):
    """Producer adapter: missing identity remains waiting, never inferred READY."""
    if not isinstance(receipt, dict): raise Refused('INVALID_RECEIPT')
    if 'test_target' in receipt:
        value = dict(validate_manifest(receipt['test_target']))
        for key in ('title', 'purpose', 'user_action', 'success_criteria'):
            if value[key] is not None: value[key] = public_text(value[key], secret_values(receipt))
        return validate_manifest(value)
    schema = json_bytes((ROOT / 'docs/governance/TEST_TARGET_MANIFEST.schema.json').read_bytes())
    lower = {key.lower(): value for key, value in receipt.items()}
    value = {key: lower.get(key) for key in schema['required']}
    value.update(schema_version=1, test_id=test_id, title=lower.get('title') or test_id,
                 created_at=lower.get('created_at') or created_at or datetime.now(timezone.utc).isoformat())
    for key in ('title', 'purpose', 'user_action', 'success_criteria'):
        if value[key] is not None: value[key] = public_text(value[key], secret_values(receipt))
    value['status'] = 'CURRENT' if str(lower.get('status', '')).startswith(
        ('READY', 'CURRENT', 'USER_ACTION_REQUIRED')) else 'WAITING_FOR_ARTIFACT'
    try:
        return validate_manifest(value)
    except Refused:
        value['status'] = 'WAITING_FOR_ARTIFACT'
        return validate_manifest(value)


def approved_receipt(source, spec, paths):
    if not isinstance(spec, dict) or set(spec) != {'receipt_path', 'receipt_sha256', 'test_id'}:
        raise Refused('INVALID_RECEIPT_REFERENCE')
    path = spec['receipt_path']
    if isinstance(path, str) and path.startswith('docs/governance/receipts/'):
        raw = source.read(path)
    else:
        raw = read_checked(path, paths)
    if raw is None or sha256(raw) != spec['receipt_sha256']: raise Refused('RECEIPT_HASH_MISMATCH')
    manifest = receipt_to_manifest(json_bytes(raw), spec['test_id'], created_at=source.timestamp)
    if manifest['test_id'] != spec['test_id']: raise Refused('RECEIPT_ID_MISMATCH')
    return manifest


def load_view(source, paths=None):
    paths = paths or TrustedPaths()
    issues, raw_files = [], {}
    for name in (BOARD, QUEUE, CURRENT, PLAN, HANDOFF, TARGET):
        try:
            raw_files[name] = source.read(name)
        except (Refused, OSError, subprocess.TimeoutExpired):
            raw_files[name] = None
        if raw_files[name] is None and name not in (TARGET, HANDOFF):
            issues.append('缺少权威文件：' + name)
    def document(name):
        try:
            value = json_bytes(raw_files[name]) if raw_files[name] is not None else {}
            if not isinstance(value, dict): raise Refused('INVALID_STATE')
            return value
        except Refused:
            issues.append('无法读取权威 JSON：' + name)
            return {}
    board, queue = document(BOARD), document(QUEUE)
    secrets = secret_values(board) + secret_values(queue)
    text = lambda value: public_text(value, secrets)
    state_text = (raw_files[CURRENT] or b'').decode('utf-8', errors='replace')
    fields = dict(re.findall(r'^([A-Z][A-Z0-9_]*)\s*=\s*([^\r\n]+)$', state_text, re.M))
    control = board.get('control_plane', {})
    extension = board.get('workbench', {})
    if not isinstance(control, dict): control = {}
    if not isinstance(extension, dict): extension = {}
    workers = []
    worker_rows = board.get('workers', {})
    if not isinstance(worker_rows, dict): worker_rows = {}
    for slot in SLOTS:
        worker = worker_rows.get(slot, {})
        if not isinstance(worker, dict): worker = {}
        status = worker.get('status')
        if not isinstance(status, str) or status not in WORKER_STATES: status = 'UNKNOWN'
        workers.append(dict(slot=slot, status=status, task_id=text(worker.get('task_id')),
            dispatch_id=text(worker.get('dispatch_id')), head=text(worker.get('delivery_commit') or worker.get('head')),
            objective=text(worker.get('objective') or worker.get('next_action')),
            blocker=text(worker.get('blocker')), next_gate=text(worker.get('next_gate')),
            summary_zh=text(worker.get('summary_zh')), receipt=text(worker.get('receipt')),
            raw_status=text(worker.get('status'))))
    workflow, actions = [], []
    tasks = queue.get('tasks', [])
    if not isinstance(tasks, list): tasks = []
    for task in tasks:
        if not isinstance(task, dict): continue
        task_id = text(task.get('task_id'))
        if task_id in LABELS or task.get('workbench_visible') is True:
            flow_state = task.get('state')
            workflow.append(dict(task_id=task_id, title=LABELS.get(task_id, text(task.get('title'))),
                status=FLOW_STATES.get(flow_state, 'UNKNOWN') if isinstance(flow_state, str) else 'UNKNOWN', reason=text(task.get('reason'))))
        action = task.get('user_action_required')
        if action and action != 'NONE':
            actions.append(dict(title=LABELS.get(task_id, task_id), instruction=text(action),
                                success_criteria=text(task.get('success_criteria'))))
    # Missing SMTP rows stay UNKNOWN. Prose about another task is not telemetry.
    for task_id in ('REAL_QQ_DELIVERY', 'REAL_SMTP_TRANSPORT'):
        if not any(item['task_id'] == task_id for item in workflow):
            workflow.insert(2, dict(task_id=task_id, title=LABELS[task_id], status='UNKNOWN', reason='尚无已登记的机器可读回执。'))
    targets = []
    specs = extension.get('test_targets', [])
    if not isinstance(specs, list): specs = []
    for spec in specs[:30]:
        try:
            targets.append(approved_receipt(source, spec, paths))
        except (Refused, OSError, TypeError, ValueError):
            issues.append('测试目标回执未通过验证。')
    active = None
    selected = extension.get('current_test_id')
    if selected:
        matches = [value for value in targets if value['test_id'] == selected]
        if len(matches) == 1: active = matches[0]
        else: issues.append('当前测试 ID 缺少唯一的已批准回执。')
    elif raw_files[TARGET] is not None:
        try: active = validate_manifest(document(TARGET))
        except Refused: issues.append('当前测试目标格式无效。')
    else:
        # Before A integrates this new contract, only the checked-in WAITING
        # bootstrap may be displayed. A local uncommitted CURRENT is never trusted.
        try:
            candidate = validate_manifest(json_bytes((ROOT / TARGET).read_bytes()))
            if candidate['status'] == 'WAITING_FOR_ARTIFACT': active = candidate
        except (Refused, OSError): pass
    # Two explicit authorities; acceptance is never inferred from CURRENT.
    development = active if active and active['test_id'] == extension.get('latest_development_test_id') else None
    qualified = next((item for item in targets if item['test_id'] == extension.get('latest_qualified_test_id')
                      and item['status'] == 'CURRENT'), None)
    qualification = None
    if active is not None and active['status'] == 'CURRENT':
        try: qualification = source.qualify(active)
        except Refused:
            issues.append('构建源码与发布登记的绑定未通过验证。')
            active = None
            development = None
    if qualified is not None:
        try:
            if source.git('merge-base', qualified['source_sha'], source.sha) != qualified['source_sha']:
                raise Refused('SOURCE_NOT_INTEGRATED')
            if source.tree_for(qualified['source_sha']) != qualified['source_tree']:
                raise Refused('SOURCE_IDENTITY_MISMATCH')
        except Refused:
            qualified = None
            issues.append('已验收候选的集成来源无法验证。')
    if active is not None:
        if not any(value['test_id'] == active['test_id'] for value in targets): targets.insert(0, active)
        if active['status'] == 'CURRENT':
            actions.append(dict(title=active['title'], instruction=active['user_action'], success_criteria=active['success_criteria']))
    if not actions and control.get('user_action_required') not in (None, 'NONE'):
        actions.append(dict(title='用户操作', instruction=text(control['user_action_required']), success_criteria='UNKNOWN'))
    try: remote = source.git('rev-parse', '--verify', 'refs/remotes/origin/integration/main3')
    except Refused: remote = 'UNKNOWN'
    phase = text(extension.get('current_phase') or fields.get('CURRENT_PHASE'))
    if (phase == 'UNKNOWN' and fields.get('GATE_A_SCHEMA_BLOCKER') == 'RESOLVED_IN_SOURCE'
            and fields.get('GATE_A_REAL_DEPLOYMENT') == 'NOT_RUN_AFTER_FIX'):
        phase = '0036 源码解阻已完成；部署验收尚未确认'
    current_gate = text(extension.get('current_gate') or fields.get('CURRENT_GATE'))
    repository = board.get('repository_url', '')
    web_url = (repository + '/blob/' + source.sha + '/' + HANDOFF
               if isinstance(repository, str) and re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) else None)
    from .devices import Devices
    return dict(project=text(board.get('project')), generation=text(board.get('generation')),
        device_profile_supported=bool(active) and Devices.source_supported(source, active.get('source_sha')),
        candidate_identity=qualification,
        current_phase=phase, current_gate=current_gate, integration_sha=source.sha, integration_tree=source.tree,
        snapshot_at=source.timestamp, snapshot_kind=text(board.get('snapshot_kind')),
        remote_head=remote, staging_status=text(extension.get('staging_status')),
        database_revision=text(extension.get('database_revision')), source_schema=text(fields.get('ALEMBIC_HEAD')),
        workers=workers, workflow=workflow, actions=actions,
        server_sync_state=text((board.get('acceptance_boundaries') or {}).get('server_sync_tier'))
            if isinstance(board.get('acceptance_boundaries', {}), dict) else 'UNKNOWN',
        formal_main3=text(control.get('formal_main3')),
        user_action_state='USER_ACTION_REQUIRED' if actions else 'NONE' if control.get('user_action_required') == 'NONE' else 'UNKNOWN',
        action_summary=('当前无需用户操作。' if not actions and control.get('user_action_required') == 'NONE'
                        else '用户操作状态 UNKNOWN' if not actions else '现在需要你完成以下操作'),
        latest_development=development, latest_qualified=qualified,
        same_artifact=bool(development and qualified and development['artifact_sha256'] == qualified['artifact_sha256']),
        active_target=active, history=targets[:20], issues=issues,
        handoff_available=raw_files[HANDOFF] is not None, web_handoff_url=web_url)


def export_projection(view, directory):
    """Disposable derived output. Never read back as approval or worker state."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (('WORKBENCH_VIEW.json', {k: v for k, v in view.items() if k != 'handoff_text'}),
                        ('ACTIVE_TEST_TARGET.json', view['active_target'])):
        temporary = directory / (name + '.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temporary.replace(directory / name)
