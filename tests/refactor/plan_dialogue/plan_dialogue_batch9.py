"""Batch 9: real plan.say, isolated business data, durable independent ¥3 ledger."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import uuid

from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.pricing import PriceCatalog
from .plan_dialogue_batch8 import batch8_cases, _new_case, _prepare_case
from .plan_dialogue_batch8_checks import evaluate_batch8, _intent_payload
from .plan_dialogue_harness import OUTPUT_ROOT, Harness, clone_tree, dump, seed_sources, utcnow, render_case
from .plan_dialogue_live import Budget, CATALOG, live_gateway, _admin_fingerprints


def cases():
    old = [c for c in batch8_cases() if c['id'] in {'H-M03', 'H-MI01', 'H-MI02', 'H-MI03'}]
    return old + [
        _new_case('I-01', '多意图', 'sources', '把截止日推迟两周，另外只学前三章。',
                  dict(intent_kinds=['update_form', 'limit_scope'], scope_include_chapter_max=3, no_search=True)),
        _new_case('I-02', '多意图', 'sources', '《Python CSV 合成教材》这份资料不要了，再找一份中文的。',
                  dict(intent_kinds=['decide_sources', 'find_sources'], card_index=0, card_status='rejected')),
        _new_case('I-03', '问与改', 'outline', '如果每天改成30分钟，会不会来不及？先别改，我只是在问。',
                  dict(intent_kinds=['question'], content_unchanged=True, no_search=True, reply_any=['分钟', '时间', '范围', '判断'])),
        _new_case('I-04', '同类多字段', 'sources', '每天改成30分钟，只安排周一、周三和周五学习。',
                  dict(intent_kinds=['update_form'], field='daily_cap', value=30, no_search=True)),
        _new_case('I-05', '修改并提问', 'ready', '把每天上限改成45分钟，顺便告诉我现在生成了多少个模块。',
                  dict(intent_kinds=['update_form', 'question'], field='daily_cap', value=45, no_search=True)),
    ]


class Batch9Budget(Budget):
    def reserve(self):
        # Preserve all settled/unknown reservations across restarts. The same
        # conservative per-HTTP Flash envelope as batch 6, with this batch's cap.
        if self.state['blocked'] or Decimal(self.state['held']) or Decimal(self.state['peak_amount']) + Decimal('2.50') > 3:
            raise ModelError('EVAL_BUDGET_STOP', '第九批预算停止，未发送模型请求。')
        self.state['held'] = '2.50'
        dump(self.path, self.state)


def evaluate(case, before, after, calls, context, error=None):
    checks = evaluate_batch8(case, before, after, calls, bridge_error=error)
    payload = _intent_payload(calls) or {}
    intents = {v.get('kind'): v for v in payload.get('intents', [])}
    def check(name, want, got):
        checks.append(dict(name=name, passed=want == got, expected=want, actual=got))
    expected = case['expected']
    fields = {expected['field']: expected['value']} if 'field' in expected else {}
    if case['id'] == 'I-04':
        fields['weekday_weights'] = [1, 0, 1, 0, 1, 0, 0]
    for key, val in fields.items():
        check('intent_field:' + key, val, intents.get('update_form', {}).get(key))
        check('host_field:' + key, val, after['draft']['form'].get(key))
    maximum = expected.get('scope_include_chapter_max')
    if maximum:
        wanted = [s['id'] for s in context['sections']
                  if (m := re.search(r'第\s*(\d+)\s*章', s['title'])) and int(m[1]) <= maximum]
        check('intent_scope', sorted(wanted), sorted(intents.get('limit_scope', {}).get('include', [])))
        check('host_scope', sorted(wanted), sorted(after['draft'].get('scope', {}).get('include', [])))
    if case['id'] == 'H-MI03':
        wanted = [m['n'] for m in context['modules'] if re.search('图表|可视化|matplotlib', m['title'])]
        check('intent_modules', wanted, intents.get('edit_modules', {}).get('delete'))
    if case['id'] == 'I-02':
        check('intent_reject', [1], intents.get('decide_sources', {}).get('reject'))
        check('intent_search_language', True, '中文' in intents.get('find_sources', {}).get('instruction', ''))
        check('host_search', True, any(c['purpose'] == 'research' and c['enable_search'] for c in calls))
    if case['id'] == 'I-05':
        count = len(before['records']['modules'])
        answer = intents.get('question', {}).get('answer', '')
        check('intent_answer_count', True, str(count) in answer or (count == 8 and '八' in answer))
    return checks


def classify(record):
    failed = [c['name'] for c in record['checks'] if not c['passed']]
    if not failed:
        return ''
    if any(c.get('error_code', '').startswith(('MODEL_CONNECTION', 'MODEL_TIMEOUT', 'EVAL_')) for c in record['calls']):
        return '连接/预算（无法判断意图）'
    return '意图判断' if any(n.startswith('intent_') for n in failed) else '宿主执行'


def run(template):
    if os.environ.get('MF_LIVE_EVAL') != '1':
        raise RuntimeError('Real requests require explicit MF_LIVE_EVAL=1')
    template = Path(template).resolve()
    if not template.is_relative_to(OUTPUT_ROOT.resolve()):
        raise RuntimeError('Reuse isolated template only')
    base = OUTPUT_ROOT / 'batch9'
    base.mkdir(exist_ok=True)
    run_dir = base / (utcnow().strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:6])
    run_dir.mkdir()
    catalog = PriceCatalog(CATALOG)
    budget = Batch9Budget(base / 'budget.json', catalog)
    admin = _admin_fingerprints()
    records = []
    with ExitStack() as stack:
        gateway = live_gateway(stack, run_dir, budget)
        sources = run_dir / 'sources'
        h = Harness(sources, gateway)
        try:
            source_id = seed_sources(h)
        finally:
            h.close()
        outline = run_dir / 'outline'
        clone_tree(template, outline)
        h = Harness(outline, gateway)
        try:
            outline_id = h.flow.drafts()[0]['plan_id']
        finally:
            h.close()
        for original in cases():
            case = deepcopy(original)
            prepared = case['prestate'] in {'ready', 'outline'}
            pid = outline_id if prepared else source_id
            root = run_dir / 'cases' / case['id']
            clone_tree(outline if prepared else sources, root / 'data')
            h = Harness(root / 'data', gateway, live_repo=False)
            gateway.case_id = case['id']
            start = len(gateway.calls)
            try:
                _prepare_case(h, pid, case)
                h.restore_pool_decisions(h.flow.load(pid))
                before = h.snapshot(pid)
                case = render_case(case, before)
                if case['id'] == 'I-01':
                    case['expected'].update(field='end', value=(date.fromisoformat(before['draft']['form']['end']) + timedelta(days=14)).isoformat())
                context = h.flow._intent_context(h.flow.load(pid))
                error = None
                try:
                    h.call('plan.say', plan_id=pid, text=case['user'])
                except Exception as exc:
                    error = dict(type=type(exc).__name__, code=getattr(exc, 'code', 'EVAL_EXCEPTION'))
                after = h.snapshot(pid)
                calls = gateway.calls[start:]
                checks = evaluate(case, before, after, calls, context, error)
                record = dict(case=case, before=before, after=after, calls=calls, context=context,
                              error=error, checks=checks, status='PASS' if all(c['passed'] for c in checks) else 'FAIL')
                record['failure_class'] = classify(record)
                dump(root / 'record.json', record)
                records.append(record)
                print(case['id'], record['status'], record['failure_class'], flush=True)
            finally:
                h.close()
        totals = gateway.summary()
    summary = dict(baseline='de1723f', records=[dict(id=r['case']['id'], status=r['status'],
        failure_class=r['failure_class'], failed=[c for c in r['checks'] if not c['passed']]) for r in records],
        budget=budget.state, usage=totals, admin_unchanged=admin == _admin_fingerprints())
    dump(run_dir / 'summary.json', summary)
    dump(base / 'latest.json', dict(run_dir=str(run_dir)))
    print('Run:', run_dir, 'Cost upper:', budget.state['peak_amount'], 'Held:', budget.state['held'], flush=True)
    return records


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-outline', required=True)
    args = parser.parse_args()
    run(args.reuse_outline)
