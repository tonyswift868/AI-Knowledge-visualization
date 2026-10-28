"""MF-03: accelerated calendar scripts through the real plans.list handler.

Expected numbers below are hand specified or simple fixed-budget arithmetic,
not copied from Ledger.status/distribute. Models, network, GUI rendering and the
system clock are not used. The registry replaces RPC transport only; handlers,
settings, CourseStore, mastery and ledger are production implementations.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import os
import tempfile
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app import handlers
from refactor.learning.app.bridge import CallContext
from refactor.learning.app.settings import AppSettings
from tests.refactor.mastery70.support import seed_assessment
from refactor.learning.course.ledger import Ledger, PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore, StoreError


ROOT = Path(os.environ.get('MF_TIMELINE_TEST_ROOT', 'D:/MentorFlow/.local/test-tmp/timeline'))
ACCOUNT = 'mfa1_' + '3' * 64
EAST = timezone(timedelta(hours=8))
WEST = timezone(timedelta(hours=-7))


class InjectedClock:
    def __init__(self):
        self.zone = EAST
        self.instant = datetime(2026, 9, 28, 4, tzinfo=timezone.utc)
        clock = self

        class DateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(clock.instant.timestamp(), tz or clock.zone)

            def astimezone(self, tz=None):
                # Emulate the OS local timezone without changing the machine.
                return super().astimezone(clock.zone if tz is None else tz)

        self.datetime = DateTime

    def set(self, value):
        if type(value) is date:
            value = datetime.combine(value, time(12), self.zone)
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        assert value.utcoffset() is not None
        self.instant = value.astimezone(timezone.utc)

    def now(self):
        return self.instant


class HandlerRegistry:
    def __init__(self):
        self.methods = {}
        self.event = _Signal()
        self.closing = _Signal()

    def push(self, name, data):
        # Handler registration binds PlanFlow's account-ready recovery callback;
        # these offline ledger scenarios never need to emit RPC events.
        return None

    def register(self, name, fn, **options):
        self.methods[name] = fn


class _Signal:
    def __init__(self):
        self.listeners = []

    def connect(self, listener):
        self.listeners.append(listener)

    def emit(self, *args):
        for listener in tuple(self.listeners):
            listener(*args)


@dataclass
class Trace:
    title: str
    expected: str
    rows: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    conclusion: str = '断言尚未全部完成；不能记为通过'


def write_report(traces):
    """One fixed artifact, overwritten rather than accumulating report copies."""
    lines = [
        '# MF-03 第七批：账本修复与今日状态时间加速复测', '',
        '本轮基线：`cdc1e0b`；保留第六批原有 15 个剧本，将两处缺陷复现改为修复断言。'
        '数据均为本机隔离测试账号，无真实用户操作、模型或网络调用。', '',
        f'模拟源码：`{Path(__file__).resolve().as_posix()}`。'
        '共享 report.md 可能被其他工位的旧版全量测试覆盖；本轮相同内容另保存在 batch7-report.md。', '',
        '读取路径：真实 `handlers.register()` 注册的 `plans.list` → '
        '`CourseStore.today_status()` → `build_today_status()`；仅替换 RPC 传输和时钟。'
        '刷新设置通过真实 `settings.save` 保存。', '',
        '表中“正式/暂记”是目标日的 FIFO 已填分钟，不是当日挂机时长。协商列是返回的 '
        '`needs_negotiation`，不等于页面已弹提示。逾期列是 `overdue_days`。完成后 '
        '`plans.list.today=null`，对应数字以“—”表示。', '',
        '逐日读数已与剧本独立预期断言；若某节标为“断言尚未全部完成”，以该次 pytest 失败为准。', '',
        '`time_completed` 只表示正式时长已满，`completed` 还要求完整课程检验通过；接口不会更改计划生命周期。'
        '`is_late_night` 表示本地时刻早于刷新点，`needs_rest` 还涵盖未退出课堂跨过刷新点；两者都不是已弹出的通知。', '',
        '完整验收输出见同目录 `batch7-full-suite.txt`，针对性回归见 `batch7-targeted.txt`，'
        '单独模拟见 `batch7-timeline-tests.txt`。`baseline-future-start.txt` 为第六批修复前失败证据，不代表当前结果。', '',
        '| 剧本 | 结论 |', '| --- | --- |',
    ]
    lines.extend(f'| {trace.title} | {trace.conclusion} |' for trace in traces)
    lines.append('')
    for trace in traces:
        lines += [f'## {trace.title}', '', f'应当显示：{trace.expected}', '',
                  '| 本地时间 | 学习日 | 操作/观察 | 目标 | 正式 | 暂记 | 欠账 | 超前分/天 | 协商 | 逾期天 | 历史欠账 | 时长/计划完成 | 深夜/休息条件 | 状态 |',
                  '| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | --- | --- | --- |']
        for r in trace.rows:
            def show(key):
                value = r.get(key)
                return '—' if value is None else ('是' if value is True else '否' if value is False else str(value))
            lines.append('| ' + ' | '.join([r['local_time'], r['day'], r['note'], show('today_target'),
                show('today_confirmed'), show('today_tentative'), show('debt_minutes'),
                show('ahead_minutes') + '/' + show('ahead_days'), show('needs_negotiation'), show('overdue_days'),
                show('history_debt_minutes'), show('time_completed') + '/' + show('completed'),
                show('is_late_night') + '/' + show('needs_rest'), r['state']]) + ' |')
        lines += ['', f'结论：{trace.conclusion}', '']
        for note in trace.notes:
            lines += [f'- {note}']
        lines += ['']
    lines += [
        '## 新接口与证据范围', '',
        '`course.today.build_today_status(ledger, *, now, refresh, tz, plan_state, check_complete, classroom_day=None)` '
        '为纯函数；`CourseStore.today_status(plan_id, *, now, refresh, tz, classroom_day=None)` '
        '在同一事务快照读取账本与掌握度，且检查课程结构是否完整。所有时间由调用方注入，无隐式取时。', '',
        '- 分钟与进度：`today_target`、`today_confirmed`、`today_tentative`、`debt_minutes`、'
        '`ahead_minutes`、`ahead_days`、`remaining_minutes`、`remaining_confirmed_minutes`、'
        '`history_debt_minutes`、`overflow_minutes`。',
        '- 判定：`needs_negotiation`、`overdue_days`、`time_completed`、`check_complete`、`completed`。',
        '- 时间：`study_day`（当前账号学习日）、`progress_day`（本次数字对应的学习日）、'
        '`is_late_night`、`needs_rest`。日期均为 ISO 字符串。',
        '- 课堂可传入开课时的 `classroom_day`，退出后不再传入；当日进度仍显示开课日，逾期天数用当前账号学习日。'
        '接口只返回休息条件，提醒去重与界面弹出仍由宿主处理。', '',
        '- 数值来自实际 Python 读取路径；未启动 WebView、未实测屏幕渲染或真实课堂。学习动作由脚本调用 '
        'CourseStore 模拟；模块检测/检验使用真实 MasteryBook 事件。',
        '- 第六批 A-01/A-02/A-05 的协商显示、窗口时钟接线和真实课堂仍交由架构师；'
        '本轮不把服务层信号或接口返回当作真实界面验证。',
        '- 本轮只改 ledger.py、store.py，新增 today.py，并改 handlers.py 的 plans.list 计算；'
        '未改生成编排、Web 页面、课堂编排或总文档。', '',
        '## 问题记录（现象、原因、解决、验证）', '',
        '| 编号 | 现象与原因 | 本轮解决 | 验证证据 |',
        '| --- | --- | --- | --- |',
        '| BUG-16 / A-06 | 创建日早于开始日，生成时按创建日重排而越出窗口 | 初始分配按窗口；重排目标从 max(今天, 开始日) 分配。冻结边界保留实际当天，并同步修正反序列化校验 | 剧本 01 实际改为窗口内 2×60；另测零预算、连续提前重排、开始日后移与历史冻结 |',
        '| BUG-16 / A-04 | 检验未过却返回逾期 0，读取层只看时长完成 | 新接口结合完整课程检验和正式时长，plans.list 统一调用；纯 Ledger.status 的时长口径不变 | 剧本 07：预算已满且检验未过仍逾期 1/2 天；全部通过才完成 |', '',
        '## 待确认项（本轮不实施偏好选择）', '',
        '- 提醒条件 Q-01：选项 A「当天尚未开始才提醒」；选项 B「当天尚未完成就提醒」。'
        '建议 A，贴合 SYS-04 原文并减少已经学习后的打扰；当前 DailyClock 的 B 行为保留，仅作为现状复测。',
        '- 历史欠账 A-03：选项 A「首页另列冻结历史欠账」；选项 B「历史明细展示，首页只列当前欠账」。'
        '建议 B，并提供历史入口，避免把已重新分配的历史欠账误当作需再次补学的分钟数。'
        '两种方式都保留 history_debt_minutes，不能直接并入当前 debt_minutes。由架构师转问用户后决定。', '',
    ]
    ROOT.mkdir(parents=True, exist_ok=True)
    document = '\n'.join(lines)
    (ROOT / 'batch7-report.md').write_text(document, encoding='utf-8')
    (ROOT / 'report.md').write_text(document, encoding='utf-8')


@pytest.fixture(scope='module')
def evidence():
    traces = []
    yield traces
    write_report(traces)


@pytest.fixture
def clock(monkeypatch):
    source = InjectedClock()
    monkeypatch.setattr(handlers, 'datetime', source.datetime)
    # Fail immediately if a future handler change tries to reach a network.
    def forbidden(*args, **kwargs):
        raise AssertionError('timeline simulations must remain offline')
    monkeypatch.setattr('socket.create_connection', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)
    return source


class Simulation:
    def __init__(self, clock, trace):
        self.clock, self.trace = clock, trace
        (ROOT / 'db').mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'db')
        self.root = Path(self.temp.name)
        self.store = CourseStore(ACCOUNT, self.root)
        self.settings = AppSettings(ACCOUNT, self.root)
        self.services = SimpleNamespace(store=self.store, settings=self.settings)
        self.changed_settings = []
        window = SimpleNamespace(services=self.services, settings_changed=self.changed_settings.append)
        self.registry = HandlerRegistry()
        handlers.register(self.registry, window)
        self.sequence = 0
        self.credit_sequence = 0

    def close(self):
        self.store.close()
        self.temp.cleanup()

    def reopen(self):
        self.store.close()
        self.store = self.services.store = CourseStore(ACCOUNT, self.root)
        self.settings = self.services.settings = AppSettings(ACCOUNT, self.root)

    def prepare(self, start, end, total, *, weights=(1,) * 7, special=None, generated=None):
        """Four sourced points plus 10 check minutes; no model-generated material."""
        self.clock.set(generated or start)
        window = PlanWindow(start, end, 180, weights, special or {})
        self.store.create_plan('plan', goal=self.trace.title, window=window)
        self.store.add_sources('plan', [dict(id='source', kind='url', url='https://example.invalid/offline',
            chunks=[dict(chunk_id='chunk', text='离线模拟专用的知识点来源。')])])
        self.store.save_outline('plan', [dict(id='stage')], [dict(id='module', stage_id='stage')])
        q, r = divmod(total - 10, 4)
        assert q > 0
        self.store.save_generated_module('plan', 'module', [dict(id=f'p{i}', summary=f'知识点 {i}',
            minutes=q + int(i < r), chunk_ids=['chunk']) for i in range(4)], check_minutes=10,
            today=generated or start)
        seed_assessment(self.store, 'plan')
        self.store.start_plan('plan', today=generated or start)
        assert self.store.get_plan('plan')['total_minutes'] == total
        return self

    def read(self):
        result = self.registry.methods['plans.list']({}, None)
        assert len(result['plans']) == 1
        assert result['plans'][0]['id'] == 'plan'
        return result['plans'][0], result['study_day']

    def observe(self, day, *, target=60, confirmed=0, tentative=0, debt=0, ahead=0, ahead_days=0,
                negotiate=False, overdue=0, time_completed=False, completed=False, history=0,
                is_late_night=False, state='active', note='读取'):
        item, actual_day = self.read()
        actual = item['today']
        self.trace.rows.append(dict(actual or {}, local_time=self.clock.instant.astimezone(self.clock.zone).isoformat(),
                                    day=actual_day, state=item['state'], note=note))
        assert actual_day == day.isoformat()
        assert item['state'] == state
        if state == 'completed':
            assert actual is None
        else:
            assert actual is not None, 'plans.list swallowed a ledger error'
            expected = dict(today_target=target, today_confirmed=confirmed, today_tentative=tentative,
                debt_minutes=debt, ahead_minutes=ahead, ahead_days=ahead_days, needs_negotiation=negotiate,
                overdue_days=overdue, completed=completed, time_completed=time_completed,
                history_debt_minutes=history, study_day=day.isoformat(), progress_day=day.isoformat(),
                is_late_night=is_late_night, needs_rest=is_late_night)
            assert {k: actual[k] for k in expected} == expected
        return actual

    def credit(self, minutes, *, tentative=False, unit='unit', day=None):
        self.credit_sequence += 1
        day = day or date.fromisoformat(self.read()[1])
        self.store.add_credit('plan', f'credit-{self.credit_sequence}', unit, minutes, day,
                              'tentative' if tentative else 'confirmed')

    def settings_save(self, **changes):
        # Match the real bridge contract: settings.save receives a cancellation
        # event even for these synchronous, offline timeline simulations.
        context = CallContext('timeline-settings-save', threading.Event(), lambda _delta: None)
        result = self.registry.methods['settings.save']({'changes': changes}, context)
        assert result['settings'] == self.changed_settings[-1] == self.settings.load()

    def detect(self, *, correct=True):
        attempt = f'attempt-{self.sequence+1}'
        for i in range(4):
            self.sequence += 1
            self.store.append_judgement('plan', Judgement(f'judgement-{self.sequence}', self.sequence,self.clock.instant,
                'module_test','module',attempt,((f'p{i}',correct or i!=0),),question_id=f'p{i}'))
        self.sequence += 1
        self.store.append_attempt_closed('plan',AttemptClosed(f'closed-{self.sequence}',self.sequence,'module',attempt,'module_test'))
        assert self.store.load_mastery('plan').module('module').state == ('passed' if correct else 'failed')

    def finish(self):
        if not self.store.load_mastery('plan').check_ready():
            self.detect()
        if self.store.get_plan('plan')['state'] == 'active':
            self.store.start_checking('plan')
        for _ in range(8):
            if self.store.load_mastery('plan').check_complete():break
            for i in range(4):
                if self.store.load_mastery('plan').question(f'p{i}').passed:continue
                self.sequence += 1
                self.store.append_judgement('plan',Judgement(f'check-{self.sequence}',self.sequence,self.clock.instant,
                    'check','module',results=((f'p{i}',True),),question_id=f'p{i}'))
        self.store.complete_plan('plan', today=date.fromisoformat(self.read()[1]))


@pytest.fixture
def simulation(clock, evidence):
    sims = []
    def create(title, expected):
        trace = Trace(title, expected)
        evidence.append(trace)
        sim = Simulation(clock, trace)
        sims.append(sim)
        return sim
    yield create
    for sim in reversed(sims):
        sim.close()


def test_timeline_future_start_respects_window_in_creation_path(simulation):
    """BUG-16 regression: generation cannot allocate before the chosen start."""
    sim = simulation('01 开始日前生成', '9 月 28–30 日目标为 0；10 月 1–2 日各 60 分。提前生成不能挪用开始日前的容量。')
    sim.prepare(date(2026, 10, 1), date(2026, 10, 2), 120, generated=date(2026, 9, 28))
    for offset in range(3):
        day = date(2026, 9, 28) + timedelta(days=offset)
        sim.clock.set(day)
        sim.observe(day, target=0, note='尚未开始，目标与欠账均为 0')
    for day in (date(2026, 10, 1), date(2026, 10, 2)):
        sim.clock.set(day)
        sim.credit(60)
        sim.observe(day, confirmed=60, time_completed=day.day == 2,
                    note='按窗口目标学习 60 分；最终检验未做，计划尚未完成')
    sim.finish()
    sim.observe(date(2026, 10, 2), state='completed', note='学习及检验完成')
    ledger = sim.store.load_ledger('plan')
    assert Ledger.from_dict(ledger.to_dict()).to_dict() == ledger.to_dict()
    sim.trace.conclusion = 'BUG-16/A-06 已修：开始日前连续三天目标/欠账均 0，窗口内两天各 60，最终检验后完成。'
    sim.trace.notes.append('无需改生成编排；Ledger.replan 将分配起点限制为 max(今天, 开始日)，数据库事件重放与序列化结果一致。')


def test_timeline_regular_weighted_days_month_boundary_and_february_29(simulation):
    """570 minutes / 9.5 full days: weekdays 60, weekend 0, Mar 4 half 30."""
    start = date(2024, 2, 26)
    targets = [60, 60, 60, 60, 60, 0, 0, 30, 60, 60, 60, 60]
    sim = simulation('02 按部就班、周末/半天、跨月与闰日', '570 分按 9.5 个全天分配：工作日 60，周末 0，3 月 4 日半天 30；3 月 8 日完成。')
    sim.prepare(start, date(2024, 3, 8), 570, weights=(1, 1, 1, 1, 1, 0, 0), special={date(2024, 3, 4): .5})
    fixed = sim.store.load_ledger('plan').targets
    assert list(fixed.values()) == targets
    for offset, target in enumerate(targets):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        sim.observe(day, target=target, note='开课前')
        if target:
            sim.credit(target)
        sim.observe(day, target=target, confirmed=target, time_completed=offset == 11, note='当天学完' if target else '休息日不学')
        assert sim.store.load_ledger('plan').targets == fixed
    sim.finish()
    sim.observe(date(2024, 3, 8), state='completed', note='检验完成并关闭计划')
    assert len(sim.store.records('credits', 'plan')) == 10
    sim.trace.conclusion = '与需求一致：周末和半天生效；闰日有 60 分目标，跨月不丢日、不重排。'


def test_timeline_three_missed_days_negotiation_threshold_and_fifo_repayment(simulation):
    """420/7=60; after one/three missed days owe 60/180; repay past first."""
    start = date(2026, 9, 28)
    sim = simulation('03 连续欠账、协商阈值与先补欠', '每天 60 分；漏 1 天欠 60，漏 2 天欠 120 并需协商，漏 3 天欠 180；先还欠账再填今天。')
    sim.prepare(start, start + timedelta(days=6), 420)
    fixed = sim.store.load_ledger('plan').targets
    for offset in range(4):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        sim.observe(day, debt=offset * 60, negotiate=offset >= 2, note='连续未学习')
    sim.credit(90)
    sim.observe(start + timedelta(days=3), debt=90, note='补 90 分，今日仍为 0')
    sim.credit(90)
    sim.observe(start + timedelta(days=3), note='再补 90 分，历史欠账归零')
    sim.credit(60)
    sim.observe(start + timedelta(days=3), confirmed=60, note='再学今日 60 分')
    for offset in range(4, 7):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        sim.credit(60)
        sim.observe(day, confirmed=60, time_completed=offset == 6, note='恢复每日 60 分')
    assert sim.store.load_ledger('plan').targets == fixed
    sim.finish()
    sim.trace.conclusion = '返回数字与需求一致；界面不一致：需协商标志未用于显示“是否安排太紧”（A-01）。'


def test_timeline_ahead_days_fixed_targets_and_early_completion(simulation):
    """180 on day 1 covers three days; another 240 on day 4 finishes early."""
    start = date(2026, 9, 28)
    sim = simulation('04 超前学习与提前完成', '每天目标始终 60；首日学 180 应超前 120 分/2 天；第四天补 240，提前 3 天结束。')
    sim.prepare(start, start + timedelta(days=6), 420)
    fixed = sim.store.load_ledger('plan').targets
    sim.credit(180)
    for offset in range(3):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        sim.observe(day, confirmed=60, ahead=(2 - offset) * 60, ahead_days=2 - offset,
                    note='首日多学 180 分' if offset == 0 else '不学，使用超前进度')
    finish = start + timedelta(days=3)
    sim.clock.set(finish)
    sim.credit(240)
    sim.observe(finish, confirmed=60, ahead=180, ahead_days=3, time_completed=True, note='再学 240 分')
    assert sim.store.load_ledger('plan').targets == fixed
    sim.finish()
    for offset in range(3, 7):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        sim.observe(day, state='completed', note='已完成，不再计入今日进行中计划')
    assert sim.store.get_plan('plan')['completed_on'] == finish.isoformat()
    sim.trace.conclusion = '与需求一致：未自动降低后续目标；正式完成后清单状态保持 completed。'


@pytest.mark.parametrize('passed', [True, False], ids=['module_passes', 'module_fails'])
def test_timeline_tentative_confirmation_or_failed_test_revoke(simulation, passed):
    """Tentative 60 is not final completion; confirm or revoke after the test."""
    start = date(2026, 9, 28)
    sim = simulation('05a 暂记转正式' if passed else '05b 检测失败撤销暂记',
        '第一天讲完暂记 60；第二天检测通过则正式 60，失败则删暂记、欠 60；补学先还欠。')
    sim.prepare(start, start + timedelta(days=4), 300)
    sim.credit(60, tentative=True, unit='p0')
    sim.observe(start, tentative=60, note='讲完答对，先暂记')
    assert sim.read()[0]['today']['remaining_confirmed_minutes'] == 300
    day = start + timedelta(days=1)
    sim.clock.set(day)
    sim.observe(day, note='暂记仍在昨天')
    sim.detect(correct=passed)
    if passed:
        assert sim.store.confirm_credits('plan', 'p0', today=day) == 60
        sim.observe(day, note='模块通过，昨天暂记转正式')
        credit = sim.store.load_ledger('plan').to_dict()['credits'][0]
        assert credit['state'] == 'confirmed' and credit['day'] == start.isoformat()
    else:
        assert sim.store.revoke_credits('plan', 'p0', today=day) == 60
        sim.observe(day, debt=60, note='模块失败，扣掉暂记')
        sim.credit(30)
        sim.observe(day, debt=30, note='补学 30，仍欠 30')
        sim.credit(60)
        sim.observe(day, confirmed=30, note='再补 60，先还 30，今天记 30')
        sim.clock.set(day + timedelta(days=1))
        sim.observe(day + timedelta(days=1), debt=30, note='次日仍欠昨天的 30')
    sim.trace.conclusion = '与需求一致：通过/失败事件和相应确认/撤销由模拟调用方依序执行，未绕过正式完成条件。'


def test_timeline_extension_freezes_history_and_redistributes_only_remaining(simulation):
    """Freeze 3*60; spread 180/6=30, keep and actually repay the unpaid 90."""
    start = date(2026, 9, 28)
    sim = simulation('06 中途延期与历史冻结', '原每天 60；前三天共记 90、欠 90。第四天延期：过去目标冻结，未来180分各30，另还原欠90。')
    sim.prepare(start, start + timedelta(days=5), 360)
    for offset, amount, completed, debt in ((0, 60, 60, 0), (1, 0, 0, 0), (2, 30, 0, 30)):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        if amount:
            sim.credit(amount)
        sim.observe(day, confirmed=completed, debt=debt, note=f'当日学 {amount} 分')
    day = start + timedelta(days=3)
    sim.clock.set(day)
    sim.observe(day, debt=90, note='延期前')
    sim.store.save_intake('plan', window=PlanWindow(start, start + timedelta(days=8), 180),
                          today=day, reason='欠账后主动延长截止日期')
    sim.observe(day, target=30, debt=90, note='延期后仍欠90，未来每天30')
    frozen = sim.store.load_ledger('plan').to_dict()['history']
    prefix = {start+timedelta(days=i):60 for i in range(3)}
    assert {d:n for d,n in sim.store.load_ledger('plan').targets.items() if d<day} == prefix
    sim.credit(120)
    sim.observe(day, target=30, confirmed=30, note='补原欠90，再完成今天30')
    for offset in range(4, 9):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        sim.credit(30)
        sim.observe(day, target=30, confirmed=30, time_completed=offset == 8, note='按新目标继续')
        assert sim.store.load_ledger('plan').to_dict()['history'] == frozen
    sim.reopen()
    assert sim.store.load_ledger('plan').to_dict()['history'] == frozen
    sim.finish()
    sim.trace.conclusion = '过去目标不重算；旧欠90持续显示并实际还清，未来每天30；重开后账本一致。'


def test_timeline_overdue_includes_unfinished_checks_until_full_completion(simulation):
    """After a two-day deadline, debt 120/overdue 1; checks still matter."""
    start = date(2026, 9, 28)
    sim = simulation('07 逾期继续补学与检验未完', '两天计划每天 60，第三天欠 120/逾期 1 天；时长用完但检验未过，应继续显示逾期，完整检验后才完成。')
    sim.prepare(start, start + timedelta(days=1), 120)
    sim.observe(start, note='第一天未学')
    sim.clock.set(start + timedelta(days=1))
    sim.observe(start + timedelta(days=1), debt=60, note='第二天仍未学')
    day = start + timedelta(days=2)
    sim.clock.set(day)
    sim.observe(day, target=0, debt=120, negotiate=True, overdue=1, note='已过截止日')
    sim.credit(120)
    sim.detect()
    sim.store.start_checking('plan')
    sim.observe(day, target=0, time_completed=True, overdue=1, state='checking',
                note='预算用完但检验未过，仍逾期 1 天且计划未完成')
    assert not sim.store.load_mastery('plan').check_complete()
    with pytest.raises(StoreError):
        sim.store.complete_plan('plan', today=day)
    day += timedelta(days=1)
    sim.clock.set(day)
    sim.sequence += 1
    sim.store.append_judgement('plan', Judgement('wrong-check', sim.sequence, sim.clock.instant, 'check',
        'module', results=(('p0', False),), question_id='p0'))
    sim.observe(day, target=0, time_completed=True, overdue=2, state='checking', note='再过一天且检验答错，仍逾期 2 天')
    for i in range(1,4):
        sim.sequence += 1
        sim.store.append_judgement('plan',Judgement(f'other-{i}',sim.sequence,sim.clock.instant,'check','module',results=((f'p{i}',True),),question_id=f'p{i}'))
    sim.sequence += 1
    sim.store.append_judgement('plan', Judgement('final-check', sim.sequence, sim.clock.instant, 'check',
        'module', results=(('p0', True),), question_id='p0'))
    sim.observe(day, target=0, time_completed=True, overdue=2, state='checking', note='答错点升到 2 级，尚未过关')
    sim.sequence += 1
    sim.store.append_judgement('plan', Judgement('passed-check', sim.sequence, sim.clock.instant, 'check',
        'module', results=(('p0', True),), question_id='p0'))
    sim.observe(day, target=0, time_completed=True, completed=True, state='checking', note='全部检验通过，接口完成条件满足但不改生命周期')
    sim.store.complete_plan('plan', today=day)
    sim.observe(day, state='completed', note='检验全过后完成')
    sim.trace.conclusion = 'BUG-16/A-04 已修：时长完成但检验未完成时仍逾期 1/2 天；答错或只升到 2 级均不清除逾期。'


def test_timeline_180_days_cross_year_leap_day_and_monthly_reopen(simulation):
    """180 days x 60 = 10800; six months of writes/readbacks and five reopens."""
    start = date(2023, 12, 1)
    end = start + timedelta(days=179)
    sim = simulation('08 连续 180 天与按月重开', '2023-12-01 起连续 180 天，每日目标/正式均 60、欠账/超前均 0；每 30 天重开数据库，跨年及 2024-02-29 不丢进度。')
    sim.prepare(start, end, 10800)
    for offset in range(180):
        day = start + timedelta(days=offset)
        sim.clock.set(day)
        sim.credit(60)
        if offset in (29, 59, 89, 119, 149):
            sim.reopen()
        sim.observe(day, confirmed=60, time_completed=offset == 179,
                    note='学 60 后关闭/重开数据库' if offset in (29, 59, 89, 119, 149) else '学 60 分')
        assert sim.read()[0]['today']['remaining_confirmed_minutes'] == (179 - offset) * 60
    assert len(sim.store.records('credits', 'plan')) == 180
    assert any(r['day'] == '2024-02-29' for r in sim.trace.rows)
    sim.finish()
    sim.observe(end, state='completed', note='六个月脚本结束并完成检验')
    sim.trace.conclusion = '与需求一致：180 天逐日读数、跨年/跨月/闰日、五次持久化重开均核对通过。'


def test_timeline_all_tentative_cannot_finish_even_after_covering_every_target(simulation):
    """120 tentative fills two days visually but is not confirmed completion."""
    start = date(2026, 9, 28)
    sim = simulation('09 全部暂记仍不能完成', '120 分全暂记可填满两天，但 remaining_confirmed_minutes 仍为 120；截止日后仍逾期，不能完成计划。')
    sim.prepare(start, start + timedelta(days=1), 120)
    sim.credit(120, tentative=True, unit='all')
    sim.observe(start, tentative=60, ahead=60, ahead_days=1, note='一次暂记全部 120 分')
    sim.clock.set(start + timedelta(days=1))
    sim.observe(start + timedelta(days=1), tentative=60, note='第二天目标暂记已填')
    day = start + timedelta(days=2)
    sim.clock.set(day)
    status = sim.observe(day, target=0, overdue=1, note='全暂记但仍然逾期，不能完成')
    assert status['remaining_minutes'] == 0 and status['remaining_confirmed_minutes'] == 120
    with pytest.raises(StoreError):
        sim.store.complete_plan('plan', today=day)
    sim.detect()
    sim.store.confirm_credits('plan', 'all', today=day)
    sim.finish()
    sim.observe(day, state='completed', note='模块通过、转正式、最终检验通过')
    sim.trace.conclusion = '与既有暂记约定一致：暂记用于浅色进度/临时抵欠，正式确认与最终检验才允许完成。'


def test_timeline_refresh_change_uses_saved_account_setting_without_rewriting_credits(simulation):
    """Mar 1 02:30 belongs to Feb 29 at 04:00 refresh, Mar 1 at 02:00."""
    sim = simulation('10 凌晨归属与修改刷新时间', '凌晨 02:30：刷新为 04:00 时属前一天，改为 02:00 后属今天；已写入的学习日不追改；02:00:00 边界归今天。')
    sim.prepare(date(2024, 2, 28), date(2024, 3, 3), 300)
    sim.clock.set('2024-03-01T02:30:00+08:00')
    sim.observe(date(2024, 2, 29), debt=60, is_late_night=True, note='刷新 04:00：属于 2 月 29 日')
    sim.credit(30)
    sim.observe(date(2024, 2, 29), debt=30, is_late_night=True, note='凌晨学 30，先补 2 月 28 日')
    sim.settings_save(refresh_time='02:00')
    sim.observe(date(2024, 3, 1), debt=90, note='改刷新为 02:00：重新读取属 3 月 1 日')
    sim.reopen()
    assert sim.settings.load()['refresh_time'] == '02:00'
    assert sim.store.records('credits', 'plan')[0]['credit']['day'] == '2024-02-29'
    sim.clock.set('2024-03-01T01:59:59+08:00')
    sim.observe(date(2024, 2, 29), debt=30, is_late_night=True, note='边界对照 01:59:59')
    sim.clock.set('2024-03-01T02:00:00+08:00')
    sim.observe(date(2024, 3, 1), debt=90, note='边界对照 02:00:00')
    sim.trace.conclusion = '读取路径与需求一致：设置持久保存并立即影响下一次读取；常开页面自动刷新仍缺接线（A-02）。'


@pytest.mark.parametrize('zone, study_date, debt_before, confirmed', [
    (WEST, date(2024, 2, 29), 0, 60), (EAST, date(2024, 3, 1), 60, 0),
], ids=['utc_minus_7', 'utc_plus_8'])
def test_timeline_same_utc_instant_uses_local_zone_not_utc_day(simulation, clock, zone, study_date, debt_before, confirmed):
    """Same UTC instant is Feb 29 23:30 in UTC-7 and Mar 1 14:30 in UTC+8."""
    clock.zone = zone
    sim = simulation(f'11 时区对照 {zone}', '同一 2024-03-01 06:30Z 瞬间：UTC-7 的学习日是 2 月 29 日，UTC+8 是 3 月 1 日。不能直接取 UTC 日期。')
    sim.prepare(date(2024, 2, 29), date(2024, 3, 2), 180)
    clock.set('2024-03-01T06:30:00+00:00')
    sim.observe(study_date, debt=debt_before, note='同一 UTC 瞬间在本地读取')
    sim.credit(60)
    sim.observe(study_date, confirmed=confirmed, note='学习 60，按本地学习日记录，仍遵守 FIFO')
    assert sim.store.records('credits', 'plan')[0]['credit']['day'] == study_date.isoformat()
    sim.trace.conclusion = '与需求一致：使用本机时区再应用刷新时间；跨月/闰日归属没有错误。'


@pytest.fixture
def daily_clock_factory(clock, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from refactor.learning.services.daily_clock import DailyClock
    app = QApplication.instance() or QApplication([])
    clocks = []

    def create(**kwargs):
        instance = DailyClock(now=clock.now, timezone=clock.zone, **kwargs)
        signals = dict(days=[], reminders=[], late=[])
        instance.study_day_changed.connect(signals['days'].append)
        instance.reminder_due.connect(lambda: signals['reminders'].append(instance.current_study_day))
        instance.late_night_notice.connect(lambda: signals['late'].append(instance.current_study_day))
        clocks.append(instance)
        return instance, signals

    yield create
    for instance in clocks:
        instance.dispose()
        instance.deleteLater()
    app.processEvents()


def test_timeline_open_class_keeps_entry_day_across_refresh(simulation, daily_clock_factory):
    """The caller pins the entry study day; clock signals never reassign credits."""
    sim = simulation('12 不退出课堂跨刷新点', '03:50 进入课堂属前一天；04:00 提示一次注意休息，不打断。未退出的后续进度仍记前一天；退出再进入才记新一天。')
    sim.prepare(date(2024, 2, 28), date(2024, 3, 3), 300)
    sim.clock.set('2024-02-29T03:50:00+08:00')
    in_class = [True]
    daily, signals = daily_clock_factory(refresh=time(4), in_class=lambda: in_class[0])
    entry = date.fromisoformat(sim.read()[1])
    assert entry == date(2024, 2, 28)
    sim.credit(30, day=entry)
    sim.observe(entry, confirmed=30, is_late_night=True, note='开课并学习 30 分')
    sim.clock.set('2024-02-29T04:00:00+08:00')
    daily.check_now()
    daily.check_now()
    assert signals['late'] == signals['days'] == [date(2024, 2, 29)]
    sim.credit(30, day=entry)
    sim.observe(date(2024, 2, 29), note='课堂未退出；新增 30 仍补到昨天，今日为 0')
    assert [p['credit']['day'] for p in sim.store.records('credits', 'plan')] == ['2024-02-28'] * 2
    pending = sim.store.pending_records(1000)
    before = sim.store.load_ledger('plan').to_dict()
    db_changes = sim.store._db.total_changes
    classroom = sim.store.today_status('plan', now=sim.clock.now(), refresh=time(4), tz=sim.clock.zone,
                                       classroom_day=entry)
    assert (classroom['study_day'], classroom['progress_day']) == ('2024-02-29', '2024-02-28')
    assert (classroom['today_target'], classroom['today_confirmed'], classroom['debt_minutes']) == (60, 60, 0)
    assert classroom['is_late_night'] is False and classroom['needs_rest'] is True
    assert classroom == sim.store.today_status('plan', now=sim.clock.now(), refresh=time(4), tz=sim.clock.zone,
                                               classroom_day=entry)
    assert sim.store._db.total_changes == db_changes
    assert sim.store.pending_records(1000) == pending
    assert sim.store.load_ledger('plan').to_dict() == before
    in_class[0] = False
    reentry = date.fromisoformat(sim.read()[1])
    in_class[0] = True
    sim.credit(60, day=reentry)
    sim.observe(date(2024, 2, 29), confirmed=60, note='退出后重进，记入新学习日')
    assert not sim.store.today_status('plan', now=sim.clock.now(), refresh=time(4), tz=sim.clock.zone,
                                     classroom_day=reentry)['needs_rest']
    assert len(signals['late']) == 1
    sim.trace.conclusion = '账本/时钟服务与需求一致；真实课堂未接入（A-05），此处开课日锁定由模拟调用方实现。'
    sim.trace.notes.append('实际服务信号：刷新日变更 1 次、深夜提醒 1 次；未验证真实界面弹窗。')
    sim.trace.notes.append('新接口实测：04:00 当前学习日 2/29，课堂 progress_day 仍为 2/28、正式 60/60、needs_rest=true；退出重进后清除跨刷新休息条件。重复读取未改数据库、账本或待同步记录。')


def test_timeline_reminders_once_daily_only_when_running_and_partial_day_ambiguity(simulation, daily_clock_factory):
    """Real DailyClock + plans.list data, advanced instantly instead of sleeping."""
    start = date(2026, 9, 28)
    sim = simulation('13 每日提醒、停用及部分学习口径', '软件运行时到点提醒且同日仅一次；完成当天目标不提醒；关闭期间不补发。已开始但未完成是否提醒，需求与服务口径需确认。')
    sim.prepare(start, start + timedelta(days=3), 240)
    sim.settings_save(reminder_enabled=True, reminder_time='20:00')
    sim.clock.set('2026-09-28T19:59:55+08:00')

    def unfinished():
        today = sim.read()[0]['today']
        return today is not None and today['today_confirmed'] + today['today_tentative'] < today['today_target']

    daily, signals = daily_clock_factory(refresh=time(4), reminder=time(20), today_unfinished=unfinished)
    sim.observe(start, note='19:59 未到提醒时间；0 次信号')
    sim.clock.set('2026-09-28T20:00:00+08:00')
    daily.check_now()
    sim.observe(start, note='20:00 尚未开始；服务提醒 1 次')
    sim.clock.set('2026-09-28T20:05:00+08:00')
    daily.check_now()
    assert signals['reminders'] == [start]
    day = start + timedelta(days=1)
    sim.clock.set(day)
    sim.credit(120)  # Repay yesterday first, then today's full target.
    for wall_time in ('19:59:55', '20:00:00'):
        sim.clock.set(f'2026-09-29T{wall_time}+08:00')
        daily.check_now()
    sim.observe(day, confirmed=60, note='今日已完成；累计仍只提醒 1 次')
    assert signals['reminders'] == [start]
    day = start + timedelta(days=2)
    sim.clock.set(day)
    sim.credit(30)
    for wall_time in ('19:59:55', '20:00:00'):
        sim.clock.set(f'2026-09-30T{wall_time}+08:00')
        daily.check_now()
    sim.observe(day, confirmed=30, note='已学 30/60：服务仍提醒；此口径待确认')
    assert signals['reminders'] == [start, day]
    sim.clock.set('2026-10-01T19:59:55+08:00')
    daily.check_now()
    daily.stop()
    sim.clock.set('2026-10-01T20:00:00+08:00')
    daily.check_now()
    sim.clock.set('2026-10-01T21:00:00+08:00')
    daily.start()
    sim.observe(start + timedelta(days=3), debt=30, note='20:00 时服务已停；21:00 重启不补发')
    assert signals['reminders'] == [start, day]
    sim.settings_save(reminder_enabled=False)
    daily.configure(reminder=None)  # Explicit service wiring, absent from the current app.
    sim.clock.set('2026-10-02T20:00:00+08:00')
    daily.check_now()
    sim.observe(start + timedelta(days=4), target=0, debt=90, overdue=1, note='提醒关闭后无新增信号')
    assert signals['reminders'] == [start, day]
    sim.trace.conclusion = '服务层去重、完成不提醒、关闭不补发均一致；窗口尚未接入（A-02），部分学习是否提醒存在 Q-01 歧义。'
    sim.trace.notes.append('服务累计提醒 2 次；本测试没有声称当前新界面已经弹出提醒。')
