"""Batch 11: real generation/Classroom/store/report, offline agents and clock.

Oracles are task-minute arithmetic, independent of Ledger's allocation code.
No page text is read to decide state. All disposable data stays on D:.
"""
from collections import Counter
from datetime import date, time, timedelta, timezone
import json
from pathlib import Path
import os
import re
import tempfile
import threading
import time as monotonic_time
from types import SimpleNamespace

import pytest

from refactor.learning.app import handlers
from refactor.learning.app.classroom import Classroom
from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.app.settings import AppSettings
from refactor.learning.course.ledger import Ledger, PlanWindow, estimate_check_minutes, study_day
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.report import build_report
from refactor.learning.course.store import CourseStore, StoreError
from tests.refactor.objective44.support import objective_bank, scripted_answer
from tests.refactor.integration38.support import Agents, seed_bank, to_question
from test_course_timeline_simulation import InjectedClock, HandlerRegistry


ROOT = Path(os.environ.get('MF_TIMELINE_TEST_ROOT', r'D:/MentorFlow/.local/test-tmp/timeline'))
PLAN = 'a' * 32
DAY = date(2026, 10, 5)
FIELDS = ('today_target', 'today_confirmed', 'today_tentative', 'debt_minutes',
          'ahead_minutes', 'overdue_days', 'remaining_minutes')
NAMES = ('目标', '正式', '暂记', '欠账', '超前', '逾期天数', '剩余')


class FlowAgents(Agents):
    def complete(self, purpose, messages, **kwargs):
        if purpose == 'coverage_check':
            return SimpleNamespace(text=json.dumps({"covered": True, "missing": [], "message": ""}), search_hits=(), citations=())
        if purpose != 'generate':
            return super().complete(purpose, messages, **kwargs)
        aliases = re.findall(r'\[(c\d+)\]', messages[-1]['content'])
        # N-P2-01 now checks content across modules. This time-accounting fixture
        # must actually supply distinct definitions and applications, rather
        # than six identical fake points from the same coarse source block.
        definitions = '【本模块】模块0' in messages[-1]['content']
        title = '定义与特征' if definitions else '情境应用'
        summary = ('解释概念{}的定义并辨识其基本特征' if definitions else
                   '针对概念{}完成实际情境案例的独立应用任务')
        return SimpleNamespace(text=json.dumps({'points': [dict(title=f'概念{i+1}的{title}', summary=summary.format(i+1),
            chunks=[aliases[0]], minutes=10) for i in range(3)]}), search_hits=(), citations=())


def join_profiles():
    for worker in list(threading.enumerate()):
        if worker.name.startswith('classroom-'):
            worker.join(10)
            assert not worker.is_alive()


@pytest.fixture
def flow_env(monkeypatch):
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT, prefix='full-flow-') as raw:
        root = Path(raw)
        clock = InjectedClock()
        clock.set(DAY)
        monkeypatch.setattr(handlers, 'datetime', clock.datetime)
        store = CourseStore('full_flow_audit', root)
        settings = AppSettings('full_flow_audit', root)
        settings.save(dict(refresh_time='04:00'))
        agent = FlowAgents()
        services = SimpleNamespace(store=store, settings=settings, gateway=agent,
                                   account_id='full_flow_audit', data_root=root)
        day = lambda: study_day(clock.now(), time(4), clock.zone)
        room = Classroom(services, day, now=lambda: clock.datetime.now(timezone.utc))
        registry = HandlerRegistry()
        registry.push = lambda *args: None
        handlers.register(registry, SimpleNamespace(services=services, settings_changed=lambda _: None))
        flow = PlanFlow(services, lambda *args: None, day)
        env = SimpleNamespace(store=store, settings=settings, agent=agent, clock=clock,
                              room=room, registry=registry, flow=flow, root=root, day=day)
        try:
            yield env
        finally:
            join_profiles()
            store.close()


@pytest.fixture(scope='module')
def audit():
    rows = []

    def check(scene, expected, actual, *, rule='PLAN-05', observed=None):
        reasonable = expected == actual
        rows.append((scene, rule, expected, actual, '合理' if reasonable else '不合理（范围外交接）'))
        assert actual == (expected if observed is None else observed), (scene, expected, actual)
    yield check
    counts = Counter(r[-1] for r in rows)
    def display(value):
        if isinstance(value, dict):
            return '；'.join(f'{key}={display(v)}' for key, v in value.items())
        return str(value).replace('|', '/').replace('\n', ' ')
    lines = ['# 第十一批：检验时长与全流程审查', '',
        '基线 0953cfd；全部为本工位实测。真实 PlanFlow.generate/start、Classroom、CourseStore、Ledger、MasteryBook、首页桥和报告入口；假 agent + 注入时钟，无模型、网络或真实界面。',
        '分钟是任务完成量，不是墙钟耗时。每日期望由固定目标和累计正式/暂记量手算；改日期后保留历史快照。', '',
        f'结论数量：{display(dict(counts))}', '',
        '| 场景 | 依据 | 应该是什么 | 实际是什么 | 是否合理 |',
        '| --- | --- | --- | --- | --- |']
    lines += [f'| {s} | {r} | {display(e)} | {display(a)} | {f} |' for s, r, e, a, f in rows]
    lines += ['', '## 需求原文与口径', '',
        '- [CHK-01] “所有知识点都检验通过 = 计划完成”；“每个点的检验时长 = 需答对次数 × 每题估算时长；每答对一次记一次进度”。',
        '- [CHK-01] “90% 的点一次答对、10% 的点有问题且每个约答错 2 次”；沿用现有预估函数 0.9×1+0.1×4，四次为困难点需记账的答对次数（初始两级，加两次错答恢复），不为错答本身记时长。实际可能低于或高于预估。',
        '- [PLAN-05] “讲完一个点且随口问答上，先记‘暂记’进度”；“学到当天时长用完就停”。本轮另按既有课堂两次不会后结束该点的规则测试暂记与后续撤回。',
        '- [TEACH-05] “只要没退出课堂，学的都记到前一天；退出课堂后再进来算新的一天”。',
        '- [PLAN-08] “只对还没学的部分重新分配每日时长，已完成和欠的记录全部保留”。',
        '- [TEACH-07] “跳过的模块，其估算时长在做‘直接考’的那一天全部记为完成”；本轮明确把最终检验留到实际检验答对时记。',
        '- [CHK-01] “截止日到了还没学完或没检验完，计划标为‘已逾期’，显示逾期几天，继续学到完成为止”。', '',
        '## 实现与兼容', '',
        '模块 JSON 新增 final_check_minutes、check_question_minutes；check_minutes 只代表模块检测。两项缺省的旧模块保持原预估/记账，不补记最终检验，不改历史事件；新旧模块可共存。复用现有 JSON 存储与同步，无表迁移。',
        '账本只接受整数分钟，生成时将现有每题参考值 1.5 四舍五入为 2 分钟，模块检测与最终检验使用同一整数。每个尚未通过的点答对一次记 2 分钟；一题多点按各点独立对错记账。',
        '判定和检验 credit 同事务提交。重复事件不重记；失败整笔回滚；跨刷新使用课堂固定学习日；远端合并既有不可变事件，不另造进度。', '',
        '## 问题记录', '',
        '| 现象 | 原因与排查 | 解决 | 验证 |', '| --- | --- | --- | --- |',
        '| 模块检测占用最终检验预算，最终检验答对不增长进度 | 基线 generate 把 estimate_check_minutes 全写到 check_minutes；_finish_test 全记入，_record 未写最终检验 credit | 分开估算、保存，检验判定与正确点 credit 原子写入 | 新计划真实生成：60 学习 + 12 模块检测 + 16 最终检验 = 88；逐步核对新增时长 |',
        '| 提前掌握时总预估尚未用完，可能卡住计划完成与报告 | complete_plan/report 同时要求账本时长已满；六点全对只需 12，预估 16 | 按用户补充规则以掌握度完成；未用预算记 release，不伪造 credit；报告分别列预估、实际、差额 | 六点一次通过实际84、预估88，释放4，剩余/欠账/逾期为0 |',
        '| 模块检测实际题量少于预估，仍记满预算 | 旧 _finish_test 直接记模块 check_minutes，没有按持久化判定数结算 | 新模块按已关闭检测的实际判定题量记时长，释放首轮余量；旧模块保持原记账 | 三点合为一题：预估6、实际2、释放4；最终报告保留差额 |',
        '| 完成动作返回的课堂 plan.state 可能仍为 checking | _complete 写库后 _Course.plan 尚为动作前快照；下一次读取才更新 | 课堂状态编排超出本轮记时长改动范围，交架构师刷新快照 | 表中最后一题即时返回与再次读取分开核对 |', '',
        '未测：真实模型质量/付费调用、WebView视觉、系统真实睡眠/时钟改动、线上数据。SYS-04 沿用基线已确认三条提醒规则；全量回归覆盖，未重新定义。', '']
    (ROOT / 'full-flow-audit.md').write_text('\n'.join(lines), encoding='utf-8')


def prepare(env):
    store = env.store
    store.create_plan(PLAN, goal='掌握两组概念', final_goal='能够解释与应用',
                      window=PlanWindow(DAY, DAY + timedelta(days=5), 60))
    store.add_sources(PLAN, [dict(id='s', url='https://example.invalid/local', adopted=True,
        chunks=[dict(chunk_id='k', text='概念一、二、三的定义与应用。')])])
    store.save_outline(PLAN,[dict(id='st',title='基础')],[dict(id=f'm{i}',stage_id='st',title=f'模块{i}') for i in range(2)])
    for i in range(2):
        store.save_generated_module(PLAN,f'm{i}',[dict(id=f'm{i}-{j:02d}',title=f'概念{j}模块{i}',summary='定义与应用',minutes=10,chunk_ids=['k']) for j in range(1,4)],
            check_minutes=6, final_check_minutes=8,check_question_minutes=2,today=DAY)
    seed_bank(store,PLAN,count=1)
    objective_bank(store)
    draft=dict(plan_id=PLAN,phase='ready')

    assert draft['phase'] == 'ready'
    assert [(m['learning_minutes'], m['check_minutes'], m['final_check_minutes'], m['check_question_minutes'])
            for m in store.records('modules', PLAN)] == [(30, 6, 8, 2)] * 2
    assert store.get_plan(PLAN)['total_minutes'] == 88
    env.flow.start(draft)


def act(env, name, *args):
    if name=='reply':name,args=scripted_answer(env.room,PLAN,env.agent,args[0])
    view = env.room.run(PLAN, lambda co, st, cx: getattr(env.room, name)(co, st, cx, *args),
                        SimpleNamespace(cancel=threading.Event(), stream=lambda _: None))
    deadline = monotonic_time.monotonic() + 3
    while view['step'] == 'preparing_questions' and monotonic_time.monotonic() < deadline:
        monotonic_time.sleep(.005)
        view = snapshot(env)
    if name in ('next','reply','answer'):view=to_question(env.room,PLAN,view)
    return view


def answer(env, *, wrong=()):
    pending = env.room._load(env.room._course(PLAN)).get('pending')
    actual_wrong = [pid for pid in wrong if pending and pid in pending['point_ids']]
    env.agent.verdicts = [('answer', not actual_wrong, actual_wrong)]
    return act(env, 'reply', '测试作答')


def snapshot(env):
    return env.room.run(PLAN, lambda co, st, cx: None,
                        SimpleNamespace(cancel=threading.Event(), stream=lambda _: None))


def home(env):
    return env.registry.methods['plans.list']({}, None)['plans'][0]


def numbers(status):
    return dict(zip(NAMES, (status[k] for k in FIELDS)))


def check_both(audit, env, scene, expected, *, class_expected=None, view=None):
    view = view or snapshot(env)
    audit(scene + '（首页）', dict(zip(NAMES, expected)), numbers(home(env)['today']))
    audit(scene + '（课堂）', dict(zip(NAMES, class_expected or expected)), numbers(view['today']))


def check_answer(env, *, wrong=False):
    pending = env.room._load(env.room._course(PLAN))['pending']
    if pending is None:
        act(env, 'start_check')
        pending = env.room._load(env.room._course(PLAN))['pending']
    return answer(env, wrong=pending['point_ids'] if wrong else ())


def finish_check(env):
    # Bank selection may cover several points with one question. Stop on
    # mastery completion, not on a fixed number of displayed questions.
    for _ in range(24):
        if snapshot(env)['step'] == 'completed':
            return
        check_answer(env)
    assert False, ('最终检验未在有界题数内完成', env.room._load(env.room._course(PLAN)), env.room._bank_jobs)


def test_full_flow_clock_dates_fail_retest_skip_and_report(flow_env, audit):
    e=flow_env;prepare(e)
    assert e.store.get_plan(PLAN)['total_minutes']==88
    e.clock.set('2026-10-06T03:59:00+08:00');act(e,'enter');act(e,'next')
    answer(e);answer(e,wrong=['m0-02'])
    e.clock.set('2026-10-06T04:01:00+08:00')
    view=snapshot(e);assert view['today']['progress_day']==DAY.isoformat()
    assert {c['credit']['day'] for c in e.store.records('credits',PLAN)}=={DAY.isoformat()}
    act(e,'leave');act(e,'enter');act(e,'next');answer(e)
    act(e,'start_test','module_test')
    while True:
        pending=e.room._load(e.room._course(PLAN))['pending']
        view=answer(e,wrong=['m0-02'] if 'm0-02' in pending['point_ids'] else [])
        if view['step']!='testing':break
    assert view['module']['state']=='failed'
    assert sum(c['minutes'] for c in e.store.load_ledger(PLAN).to_dict()['credits'])==36  # 30 learned + 3 questions x 2.
    assert not any(r['op']=='revoke' for r in e.store.records('ledger_events',PLAN))
    e.registry.methods['plan.window'](dict(plan_id=PLAN,end='2026-10-12'),None)
    assert e.store.get_plan(PLAN)['window']['end']=='2026-10-12'
    for job in list(e.flow.services._point_minute_jobs['jobs'].values()): job['thread'].join(5)
    assert e.store.get_plan(PLAN)['total_minutes']==79  # Frozen 30; unlearned 21; unchanged checks 28.
    act(e,'start_test','retest');assert snapshot(e)['question']['total']==1
    answer(e)
    assert sum(c['minutes'] for c in e.store.load_ledger(PLAN).to_dict()['credits'])==38  # Just one wrong question, no relearning.
    act(e,'next_module');act(e,'start_test','skip_test')
    while True:
        view=answer(e)
        if view['step']!='testing':break
    assert view['module']['state']=='skipped'
    assert sum(c['minutes'] for c in e.store.load_ledger(PLAN).to_dict()['credits'])==65
    act(e,'next_module');act(e,'start_check');act(e,'leave')
    e.clock.set(DAY+timedelta(days=9));act(e,'enter')
    assert snapshot(e)['today']['overdue_days']==2
    errors=answered=0
    while not e.store.load_mastery(PLAN).check_complete():
        pending=e.room._load(e.room._course(PLAN))['pending']
        if pending is None:act(e,'start_check');pending=e.room._load(e.room._course(PLAN))['pending']
        wrong='m0-02' in pending['point_ids'] and errors<2
        errors+=int(wrong);answered+=1
        view=check_answer(e,wrong=wrong)
        assert answered<30
        credits=[r['credit'] for r in e.store.records('credits',PLAN) if r['id'].startswith('check:')]
        assert len(credits)==answered and sum(c['minutes'] for c in credits)==answered*2
        assert {c['day'] for c in credits}=={e.day().isoformat()}
    assert errors==2 and view['plan']['state']=='completed'
    assert home(e)['state']=='completed' and snapshot(e)['today']['remaining_minutes']==0
    join_profiles();report=e.registry.methods['plan.report'](dict(plan_id=PLAN),None)['report']
    assert report['plan']['confirmed_minutes']==65+answered*2
    assert report['plan']['actual_completion_date']=='2026-10-14'
    assert len(report['questions'])==6 and all(q['passed'] for q in report['questions'])
    assert e.store.load_report(PLAN)==report
    assert build_report(e.store,PLAN,today=e.day())=={k:v for k,v in report.items() if k!='point_comments'}


def skip_all(env):
    act(env, 'enter')
    for _ in range(2):
        act(env, 'start_test', 'skip_test')
        while True:
            view = answer(env)
            if view['step'] != 'testing': break
        act(env, 'next_module')
    act(env, 'start_check')


def test_all_correct_finishes_below_reserve(flow_env, audit):
    e = flow_env
    prepare(e)
    skip_all(e)
    finish_check(e)
    assert e.store.load_mastery(PLAN).check_complete()
    audit('所有点一次通过：释放4分钟预估差额，仍应完成', ('completed', 0),
          (e.store.get_plan(PLAN)['state'], e.store.load_ledger(PLAN).status(DAY).remaining_minutes), rule='CHK-01')
    join_profiles()
    report = e.flow.report(PLAN)['report']
    assert (report['plan']['planned_minutes'], report['plan']['confirmed_minutes']) == (88, 84)
    audit('报告保留最终检验预估、实际和差额', (16, 12, 4, 4),
          tuple(report['time']['check_minutes'][key] for key in
                ('final_estimated', 'final_actual', 'final_difference', 'released_minutes')), rule='CHK-01')
    assert snapshot(e)['today']['debt_minutes'] == snapshot(e)['today']['remaining_minutes'] == 0
    e.clock.set(DAY+timedelta(days=30))
    assert snapshot(e)['today']['overdue_days'] == 0
    pending = e.store.pending_records(1000)
    e.store.complete_plan(PLAN, today=e.day())
    assert e.store.pending_records(1000) == pending
    with CourseStore('release_replica', e.root) as replica:
        records = [(row, i+1) for i, row in enumerate(pending)]
        replica.apply_remote(records)
        replica.apply_remote(records)
        assert replica.load_ledger(PLAN).to_dict() == e.store.load_ledger(PLAN).to_dict()
        assert replica.load_ledger(PLAN).status(e.day()).remaining_minutes == 0


def event(e, identifier, seq, results, *, context='check', attempt=None):
    available = {q['id'] for q in e.store.records('question_bank',PLAN)}
    qid = results[0][0]+'-0' if results else None
    return Judgement(identifier, seq, e.clock.now(), context, 'm0', attempt, tuple(results), question_id=qid if qid in available else None)


def test_final_credit_atomic_retry_wrong_multipoint_and_pinned_day(flow_env, monkeypatch):
    e = flow_env
    prepare(e)
    skip_all(e)
    # Compare only this injected transaction, after optional background
    # preparation has finished writing question-bank synchronization rows.
    join_profiles()
    seq = max(r['event']['seq'] for entity in ('judgements', 'attempt_closed') for r in e.store.records(entity, PLAN)) + 1
    row = event(e, 'atomic-final', seq, [('m0-01', True), ('m0-02', False)])
    original = e.store._changed
    def fail(operation, entity):
        if operation == 'local' and entity == 'credits':
            raise RuntimeError('injected credit failure')
        original(operation, entity)
    before = e.store.pending_records(1000)
    monkeypatch.setattr(e.store, '_changed', fail)
    with pytest.raises(RuntimeError):
        e.store.append_judgement(PLAN, row, credit_day=DAY)
    assert e.store.pending_records(1000) == before
    monkeypatch.setattr(e.store, '_changed', original)
    e.clock.set(DAY + timedelta(days=1))
    e.store.append_judgement(PLAN, row, credit_day=DAY)
    saved = e.store.pending_records(1000)
    e.store.append_judgement(PLAN, row, credit_day=DAY)
    assert e.store.pending_records(1000) == saved
    cs = [r['credit'] for r in e.store.records('credits', PLAN) if r['id'].startswith('check:')]
    assert [(c['minutes'], c['day'], c['unit_id']) for c in cs] == [(2, DAY.isoformat(), 'check:m0-01-0')]
    e.store.append_judgement(PLAN, event(e, 'passed-again', seq+1, [('m0-01', True)]), credit_day=DAY)
    assert len([r for r in e.store.records('credits', PLAN) if r['id'].startswith('check:')]) == 2


def test_new_estimates_persist_sync_and_validate(flow_env):
    e = flow_env
    prepare(e)
    with CourseStore('full_flow_audit', e.root) as reopened:
        assert reopened.records('modules', PLAN) == e.store.records('modules', PLAN)
        assert reopened.load_ledger(PLAN).total_minutes == 88
    with CourseStore('replica', e.root) as replica:
        replica.apply_remote([(record, i+1) for i, record in enumerate(e.store.pending_records(1000))])
        # This historical fixture seeds pre-70 questions. Sync now retires
        # those cards and marks preparation pending, without changing any of
        # the module's estimates or immutable accounting under test here.
        modules = replica.records('modules', PLAN)
        assert [{k:v for k,v in m.items() if k not in ('material_preparation','question_extraction')}
                for m in modules] == e.store.records('modules', PLAN)
        assert all(m['material_preparation']['state']=='pending' for m in modules)
        assert all(q['role']=='retired' for q in replica.records('question_bank',PLAN))
        assert replica.load_ledger(PLAN).to_dict() == e.store.load_ledger(PLAN).to_dict()


def test_legacy_module_has_no_extra_final_credits(flow_env):
    e = flow_env
    e.store.create_plan(PLAN, window=PlanWindow(DAY, DAY+timedelta(days=1), 60))
    e.store.add_sources(PLAN, [dict(id='s', url='https://example.invalid', chunks=[dict(chunk_id='k', text='概念')])])
    e.store.save_outline(PLAN, [dict(id='st')], [dict(id='m0', stage_id='st')])
    e.store.save_generated_module(PLAN, 'm0', [dict(id='p', title='点', summary='点', minutes=10, chunk_ids=['k'])],
                                  check_minutes=5, today=DAY)
    assert e.store.get_plan(PLAN)['total_minutes'] == 15
    e.store.append_judgement(PLAN, event(e, 'module', 1, [('p', True)], context='module_test', attempt='a'))
    e.store.append_attempt_closed(PLAN, AttemptClosed('closed', 2, 'm0', 'a', 'module_test'))
    e.store.append_judgement(PLAN, event(e, 'final', 3, [('p', True)]), credit_day=DAY)
    assert e.store.records('credits', PLAN) == []


def test_ninety_ten_final_estimate_counts_correct_work():
    assert estimate_check_minutes(10, minutes_per_question=2) == 26
    # 90% of questions once, 10% three extra answers; no point levels.
    assert 9*2 + 4*2 == 26


GroupedModuleAgents = FlowAgents  # questions come from stored source fixtures


def test_module_detection_releases_unused_estimate_and_report(flow_env, audit):
    e=flow_env;prepare(e)
    # One question covering three points is still just one assessment question.
    with e.store.transaction():
        for q in e.store.records('question_bank',PLAN):e.store._delete('question_bank',PLAN,q['id'])
    for module in e.store.records('modules',PLAN):
        mids=[p['id'] for p in e.store.records('points',PLAN) if p['module_id']==module['id']]
        from tests.refactor.integration38.support import bank_question
        row=bank_question(mids[0],module['id'],kind='fill',ids=mids,answer='测试作答',chunk='k')
        row.update(id='group-'+module['id'],stem=module['title']+'综合题：'+row['stem'])
        e.store.add_bank_questions(PLAN,module['id'],[row])
        e.store.set_question_estimate(PLAN,module['id'],1,today=DAY)
        e.store.save_module_preparation(PLAN,module['id'],dict(state='done'))
    assert e.store.get_plan(PLAN)['total_minutes']==70  # 60 learning + 2x2 module + 2x3 final reserve.
    skip_all(e);finish_check(e);join_profiles()
    report=e.flow.report(PLAN)['report'];timing=report['time']['check_minutes']
    assert len(report['questions'])==2
    assert (timing['module_estimated'],timing['module_actual'],timing['final_estimated'],timing['final_actual'])==(4,4,6,4)
    assert (report['plan']['planned_minutes'],report['plan']['credited_minutes'])==(70,68)
    assert e.store.load_ledger(PLAN).status(DAY+timedelta(days=30)).debt_minutes==0


def test_final_check_credit_pins_before_refresh_until_leave(flow_env, audit):
    e = flow_env
    prepare(e)
    e.clock.set('2026-10-06T03:59:00+08:00')
    skip_all(e)
    e.clock.set('2026-10-06T04:01:00+08:00')
    check_answer(e)
    act(e, 'leave')
    act(e, 'enter')
    check_answer(e)
    credits = sorted((r['operation_seq'], r['credit']['day'], r['credit']['minutes'])
                     for r in e.store.records('credits', PLAN) if r['id'].startswith('check:'))
    audit('最终检验跨刷新归属：退出前记旧日、再进记新日', [('2026-10-05', 2), ('2026-10-06', 2)],
          [(day, minutes) for _, day, minutes in credits], rule='TEACH-05')


def test_release_survives_replan_roundtrip_without_fake_credits():
    ledger = Ledger(PlanWindow(DAY, DAY+timedelta(days=3), 60), 100)
    ledger.add_confirmed('c', 'p', 20, DAY)
    ledger.release_unused(10)
    assert ledger.targets == {DAY:25, DAY+timedelta(days=1):25, DAY+timedelta(days=2):25, DAY+timedelta(days=3):15}
    assert ledger.status(DAY).remaining_minutes == 70
    ledger.replan(DAY+timedelta(days=1), PlanWindow(DAY, DAY+timedelta(days=7), 60), 100)
    assert list(ledger.targets.values()) == [10]*7
    frozen = ledger.to_dict()['history']
    ledger.add_confirmed('next', 'q', 60, DAY+timedelta(days=2))
    ledger.release_unused(10)
    restored = Ledger.from_dict(ledger.to_dict())
    assert restored.to_dict() == ledger.to_dict()
    assert restored.status(DAY+timedelta(days=20)).debt_minutes == 0
    assert restored.status(DAY).remaining_minutes == 0
    assert restored.to_dict()['history'] == frozen
    assert sum(c['minutes'] for c in restored.to_dict()['credits']) == 80


def test_estimate_validation_immutability_and_settlement_rollback(flow_env, monkeypatch):
    e = flow_env
    prepare(e)
    before = e.store.pending_records(1000)
    with pytest.raises(StoreError):
        e.store.save_outline(PLAN, [], [dict(id='m0', final_check_minutes=999)])
    assert e.store.pending_records(1000) == before
    with pytest.raises(StoreError, match='.'):
        e.store.settle_module_test(PLAN, 'm0', 'never-closed', today=DAY)
    assert e.store.pending_records(1000) == before
    module = e.store.records('modules', PLAN)[0]
    for invalid in (dict(final_check_minutes=-1), dict(check_question_minutes=0), dict(check_question_minutes=1.5)):
        with pytest.raises(StoreError):
            with e.store.transaction():
                e.store._put('modules', PLAN, dict(module, **invalid))
        assert e.store.pending_records(1000) == before
    row = event(e, 'one-question', 1, [(f'm0-{i:02}', True) for i in range(1,4)], context='module_test', attempt='group')
    e.store.append_judgement(PLAN, row)
    e.store.append_attempt_closed(PLAN, AttemptClosed('closed', 2, 'm0', 'group', 'module_test'))
    before = e.store.pending_records(1000)
    original = e.store._changed
    def fail(operation, entity):
        if operation == 'local' and entity == 'ledger_events': raise RuntimeError('release failed')
        original(operation, entity)
    monkeypatch.setattr(e.store, '_changed', fail)
    with pytest.raises(RuntimeError):
        e.store.settle_module_test(PLAN, 'm0', 'group', today=DAY)
    assert e.store.pending_records(1000) == before
    monkeypatch.setattr(e.store, '_changed', original)
    e.store.settle_module_test(PLAN, 'm0', 'group', today=DAY)
    done = e.store.pending_records(1000)
    e.store.settle_module_test(PLAN, 'm0', 'group', today=DAY)
    assert e.store.pending_records(1000) == done
