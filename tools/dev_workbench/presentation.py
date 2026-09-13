"""Chinese summaries derived from the unchanged authority view; no launch policy."""
import re

from .authority import public_text
from .resources import Resources, ICONS

# These are semantic stage names, not facts about their current status. Specific
# acceptance evidence takes priority over a broader planning gate when present.
STAGES = {
    'login': ('REAL_NATIVE_LOGIN', 'GATE_A_AUTH_LIVE_ACTIVATION'),
    'continuity': ('OLD_DATA_CONTINUITY', 'DATA_CONTINUITY', 'ACCOUNT_DATA_CONTINUITY'),
    'account': ('ACCOUNT_LIFECYCLE_ACCEPTANCE', 'GATE_B_ACCOUNT_SECURITY'),
    'update': ('FINAL_INSTALL_UPDATE', 'GATE_C_UPDATE_FINAL_CANDIDATE'),
    'formal': ('MAIN3_PROMOTION',),
}
ENGINEERING = re.compile(r'(?:[a-fA-F0-9]{16,}|[A-Za-z]:[\\/]|[A-Z][A-Z0-9]*_[A-Z0-9_]+|\b(?:SHA|TREE|TASK_ID|DISPATCH_ID)\b)')


def sentence(value, fallback, *, limit=60):
    """Raw machine identities remain in details, never transliterated or truncated there."""
    if not isinstance(value, str) or not re.search(r'[\u3400-\u9fff]', value) or ENGINEERING.search(value):
        return fallback
    cleaned = public_text(value).splitlines()[0].strip()
    return cleaned if len(cleaned) <= limit else cleaned[:limit-1] + '…'


def short_path(value):
    if not isinstance(value, str) or not value: return ''
    pieces = re.split(r'[\\/]', value)
    return pieces[0] + '\\…\\' + pieces[-1] if len(pieces) > 2 else value


def details_payload(value):
    """Keep full schema-bounded identities while retaining V1 public-text redaction."""
    if isinstance(value, str): return public_text(value)
    if isinstance(value, dict): return {key: details_payload(child) for key, child in value.items()}
    if isinstance(value, list): return [details_payload(child) for child in value]
    return value


def summarize(view, resources=None):
    r = resources or Resources()
    active = view.get('active_target') or {}
    target_state = ('READY' if active.get('status') == 'CURRENT' else
                    'WAITING' if active.get('status') == 'WAITING_FOR_ARTIFACT' else 'NONE')
    rows = {row['task_id']: row for row in view.get('workflow', [])}
    workflow = []
    for stage, ids in STAGES.items():
        evidence = next((rows[key] for key in ids if key in rows), None)
        status = evidence.get('status', 'UNKNOWN') if evidence else 'UNKNOWN'
        if stage == 'formal' and view.get('formal_main3') == 'NOT_CREATED': status = 'NOT_CREATED'
        status_text = r.text('workflow', 'not_started') if status == 'NOT_RUN' else r.text('statuses', status)
        workflow.append(dict(id=stage, title=r.text('workflow', stage), status=status,
                             text=ICONS.get(status, '?') + ' ' + status_text))
    workers = []
    for row in view.get('workers', []):
        fallback = r.text('empty', 'standby_summary' if row['status'] == 'SILENT' else 'summary')
        summary = row.get('summary_zh')
        if not summary or summary == 'UNKNOWN': summary = row.get('objective')
        workers.append(dict(slot=row['slot'], status=row['status'], text=r.text('statuses', row['status']),
            summary=sentence(summary, fallback, limit=42)))
    actions = view.get('actions') or []
    required = bool(actions)
    confirmed_none = view.get('user_action_state') == 'NONE' or view.get('action_summary') == '当前无需用户操作。'
    if required:
        # The primary launch button describes the candidate it will actually launch.
        action = dict(title=active.get('title'), instruction=active.get('user_action')) if target_state == 'READY' else actions[0]
        action_title = sentence(action.get('title'), r.text('empty', 'action_title'), limit=32)
        action_sentence = sentence(action.get('instruction'), r.text('empty', 'action_sentence'), limit=70)
    elif confirmed_none:
        action_title = r.text('empty', 'no_action')
        action_sentence = r.text('empty', 'no_action_running' if any(w['status'] == 'RUNNING' for w in workers) else 'no_action_snapshot')
    else:
        action_title, action_sentence = r.text('empty', 'action_unknown'), r.text('empty', 'action_unknown_hint')
    phase = view.get('current_phase')
    if phase == '0036 源码解阻已完成；部署验收尚未确认':
        phase = r.text('messages', 'phase_schema_pending')
    phase = sentence(phase, r.text('empty', 'phase'), limit=32)
    blocked = [stage['title'] for stage in workflow if stage['status'] == 'BLOCKED' and stage['id'] != 'formal']
    blocked += [r.text('labels', 'worker_blocker', slot=worker['slot']) for worker in workers if worker['status'] == 'BLOCKED']
    overall = ('USER_ACTION_REQUIRED' if required else 'BLOCKED' if blocked else
               'WAITING_FOR_ARTIFACT' if target_state == 'WAITING' else
               'NORMAL' if any(s['status'] in ('RUNNING', 'PASS') for s in workflow)
               or any(w['status'] == 'RUNNING' for w in workers) else 'UNKNOWN')
    return dict(phase=phase, overall=r.text('statuses', overall), overall_state=overall,
        integration_short=(view.get('integration_sha') or '')[:8], action_required=required,
        action_title=action_title, action_sentence=action_sentence, target_state=target_state,
        target_text=r.text('statuses', target_state),
        purpose=sentence(active.get('purpose'), r.text('empty', 'purpose')),
        version=(active['product_version'] if isinstance(active.get('product_version'), str)
                 and re.fullmatch(r'[0-9A-Za-z.+-]{1,32}', active['product_version'])
                 and active['product_version'] != 'UNKNOWN' else r.text('empty', 'unknown_value')),
        workflow=workflow, workers=workers, blocked=blocked)
