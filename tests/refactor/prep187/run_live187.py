"""Opt-in BUG-121 verification against an isolated read-only SQLite backup.

No credential values or request/source text are printed or recorded here.
Run as a module with --live --account-id ... --preset-root ... .
"""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import ModelError
from refactor.learning.connections.gateway import MODEL_MAX_OUTPUT, PURPOSE_MIN_OUTPUT
from refactor.learning.course.point_materials import preparation_done, prepare_module
from refactor.learning.course.store import CourseStore
from tests.refactor.simulation.run import live_factory

PLAN = '029b076315034c00bbd6842a5c9c31d9'
MODULE = '17080281e3c84e51'
ROOT = Path('D:/MentorFlow/.local/test-tmp/prep187')


class BudgetGateway:
    """Reserve a full worst-case call, not an average observed invoice."""
    def __init__(self, real, root, *, resume=False):
        self.real, self.root = real, root
        self.lock = threading.RLock()
        previous = root/'calls-safe.json'
        self.calls = json.loads(previous.read_text(encoding='utf-8')) if resume and previous.exists() else []
        self.spent = sum((Decimal(c['charged_bound']) for c in self.calls), Decimal(0))
        self.halted = False

    def __getattr__(self, name):
        return getattr(self.real, name)

    def complete(self, purpose, messages, **kwargs):
        with self.lock:
            if (self.halted or purpose not in {'point_materials', 'distractor_check'}
                    or kwargs.get('plan_id') != PLAN):
                raise ModelError('LIVE187_SCOPE', '本次只核对第7模块的讲义和备题。')
            config = self.real.get_configuration()
            tokens = MODEL_MAX_OUTPUT.get(config['model'], max(kwargs.get('max_tokens', 4096), PURPOSE_MIN_OUTPUT.get(purpose, 0)))
            upper = len((kwargs.get('system', '') + json.dumps(messages, ensure_ascii=False)).encode('utf-8')) + 4096
            context = dict(self.real.estimate_context(), price_period='peak', cache_mode='implicit')
            estimate = self.real.price_catalog.estimate_cost(config['provider_id'], config['model'],
                input_tokens=(upper, upper), output_tokens=(tokens, tokens), cache_hit_rate=(0, 0), context=context)
            if estimate is None or estimate.currency != 'CNY' or self.spent + estimate.high > Decimal('3'):
                self.halted = True
                raise ModelError('LIVE187_BUDGET', '下一次最高费用预留会超过本次¥3上限，未发送。')
            began = time.monotonic()
            result = error = None
            now = datetime.now(timezone.utc)
            try:
                result = self.real.complete(purpose, messages, **kwargs)
                return result
            except Exception as exc:
                error = exc
                raise
            finally:
                usage = getattr(result, 'usage', None) if result is not None else getattr(error, 'usage', None)
                cost = self.real.price_catalog.cost(config['provider_id'], config['model'], usage,
                    at=now, context=context) if usage is not None else None
                charged = cost.amount if cost is not None else estimate.high
                actual_context = dict(self.real.estimate_context(), cache_mode='implicit')
                actual = self.real.price_catalog.cost(config['provider_id'], config['model'], usage,
                    at=now, context=actual_context) if usage is not None else None
                self.spent += charged
                # Unknown usage already consumes the FULL reserved bound.
                # Remaining calls are safe only if their full bounds still fit.
                if self.spent >= Decimal('3'):
                    self.halted = True
                self.calls.append(dict(purpose=purpose, seconds=round(time.monotonic()-began, 3),
                    state='failed' if error else 'succeeded', code=getattr(error, 'code', None),
                    peak_cny=str(cost.amount) if cost is not None else None,
                    time_cny=str(actual.amount) if actual is not None else None, charged_bound=str(charged)))
                (self.root/'calls-safe.json').write_text(json.dumps(self.calls, ensure_ascii=False, indent=2), encoding='utf-8')


def counts(store):
    points = [p for p in store.records('points', PLAN) if p['module_id'] == MODULE]
    rows = [q for q in store.records('question_bank', PLAN) if q['module_id'] == MODULE]
    states, reasons = {}, {}
    for point in points:
        for outcome in point.get('subpoint_preparation', {}).get('processed_subpoints', {}).values():
            state = outcome.get('status', 'unknown') if isinstance(outcome, dict) else str(outcome)
            states[state] = states.get(state, 0) + 1
        for rejection in point.get('subpoint_preparation', {}).get('rejections', []):
            reason = rejection.get('reason', '未说明')
            reasons[reason] = reasons.get(reason, 0) + 1
    return dict(points=len(points), subpoints=sum(len(p.get('subpoints', [])) for p in points),
        outcomes=states, rejections=reasons, bank=sum(q.get('role') != 'retired' for q in rows),
        question_subpoints=len({q.get('subpoint_id') for q in rows if q.get('role') != 'retired' and q.get('subpoint_id')}),
        preparation_done=preparation_done(store, PLAN, MODULE))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Continue the existing isolated copy and cumulative budget')
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--preset-root', type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        print('SKIP: --live required')
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    data = ROOT/'data'
    target = data/'learning'/'accounts'/args.account_id/'course.sqlite3'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.resume:
        parser.error('Verification copy already exists; do not overwrite or silently rerun paid work.')
    if args.resume and not target.exists():
        parser.error('No isolated copy to resume.')
    source = Path('D:/MentorFlowApp/MentorFlow/Data/learning/accounts')/args.account_id/'course.sqlite3'
    if not args.resume:
        with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    real = live_factory(args.preset_root, args.account_id)(SimpleNamespace(account_id=args.account_id, data_root=data))
    gateway = BudgetGateway(real, ROOT, resume=args.resume)
    store = CourseStore(args.account_id, data)
    services = SimpleNamespace(store=store, gateway=gateway, account_id=args.account_id, data_root=data, _closed=False)
    flow = PlanFlow(services, lambda *_: None, date.today)
    # This isolated verification is for module 7 only, not unrelated point-minute work.
    flow.ensure_point_minutes = lambda *_: None
    prior_path = ROOT/'live-result.json'
    prior = json.loads(prior_path.read_text(encoding='utf-8')) if args.resume and prior_path.exists() else None
    summary = dict(before=prior['before'] if prior else counts(store),
                   resumed_from=counts(store) if prior else None,
                   previous_seconds=prior.get('previous_seconds', 0)+prior.get('seconds', 0) if prior else 0,
                   expected='第7模块按现有终态断点续备，慢首字不被丢；完整结束后preparation_done=True，无用户原库写入。',
                   test_output_cap=32768, budget_cny='3')
    (ROOT/'live-result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    began = time.monotonic()
    job = None
    try:
        # Same test-only cap used by run_live121; product output maximum is unchanged.
        with patch.dict(MODEL_MAX_OUTPUT, {'deepseek-v4-flash':32768, 'deepseek-flash':32768}):
            module = next(m for m in store.records('modules', PLAN) if m['id'] == MODULE)
            module = dict(module, points=[p for p in store.records('points', PLAN) if p['module_id'] == MODULE])
            job = flow._prepare_remaining(PLAN, [module], prepare_module, retry=True, priority=True)
            while PLAN in flow._preparing:
                time.sleep(.25)
            summary.update(status=job['status'], error=job.get('error'), code=job.get('code'), after=counts(store))
    except BaseException as exc:
        summary.update(error_code=getattr(exc, 'code', type(exc).__name__))
        if job is not None:
            job['cancel'].set()
        raise
    finally:
        services._closed = True
        summary.update(seconds=round(time.monotonic()-began, 3), peak_cost_bound_cny=str(gateway.spent), calls=gateway.calls)
        (ROOT/'live-result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        store.close()
        real.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
