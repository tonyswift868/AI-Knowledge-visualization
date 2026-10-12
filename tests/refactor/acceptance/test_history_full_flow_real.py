"""Batch 24 history acceptance. No real calls unless MF_HISTORY_REAL=1.

Run directly with MF_HISTORY_PHASE=1, then manually audit phase-one evidence
before phase 2. Product code is never edited; clocks and fault boundaries are
injected into the isolated acceptance process only.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import random
import sys
import threading
import time
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from tests.refactor.acceptance import test_full_flow_real as base
from tests.refactor.class_eval import class_eval as ce
from refactor.learning.app.classroom import Classroom
from refactor.learning.connections.errors import ModelError
from refactor.learning.course.judge_agent import _json_object

OUT = Path(r"D:\MentorFlow\.local\test-tmp\acceptance2")
GOAL = "唐朝与丝绸之路（中学历史水平）：长安—天山廊道的路线与主要节点、唐代的对外交流、丝路上的商品与文化传播"
FALLBACK = "https://webarchive.unesco.org/web/20230311124916/https://whc.unesco.org/en/list/1442"


class HistoryGateway(ce.RecordedGateway):
    """Account-local journal, including genuine search and its returned sources.

Reserve at peak catalogue rates before calling. Pending/unknown calls retain
their reserve. Searches reserve additional server-tool input/output; reserve
is a conservative budget meter, never represented as the vendor invoice.
"""
    phase = 1
    study_date = "setup"

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def complete(self, purpose, messages, **kw):
        maximum = Decimal('2.5') if self.phase == 1 else Decimal('5')
        packet = json.dumps(dict(system=kw.get('system'), messages=messages), ensure_ascii=False)
        bound = len(packet.encode('utf-8')) + 2048
        search = kw.get('enable_search', False)
        output_bound = kw.get('max_tokens', 4096)
        if search:
            bound += 250000
            output_bound *= 10
        context = dict(self.inner.estimate_context(), cache_mode='implicit', price_period='peak')
        estimate = self.inner.price_catalog.estimate_cost(self.config['provider_id'], self.config['model'],
            input_tokens=(bound, bound), output_tokens=(output_bound, output_bound), cache_hit_rate=(0, 0), context=context)
        with self.lock:
            spent = self.spent()
            if estimate is None or estimate.currency != 'CNY' or spent + estimate.high > maximum:
                raise ModelError('EVAL_BUDGET_STOP', f'本段预算上限 ¥{maximum}；调用前已停止。')
            identifier = f'call-{len(self.rows)+1:04d}'
            row = dict(id=identifier, at=ce.now().isoformat(), day=self.study_date,
                       purpose=purpose, reserved=str(estimate.high), status='pending')
            self.rows.append(row)
            ce.dump(self.budget_path, self.rows)
        entry = dict(row, system=kw.get('system'), messages=copy.deepcopy(messages),
                     max_tokens=kw.get('max_tokens', 4096), enable_search=search)
        # Background requests can still be in flight when a budget stop ends
        # the process. Preserve their request and reserve before networking.
        ce.dump(self.output/'calls'/(identifier+'.json'), entry)
        started, first, usage = time.monotonic(), [], None
        on_text = kw.get('on_text')
        if on_text:
            def stream(text):
                if text and not first: first.append(time.monotonic() - started)
                on_text(text)
            kw['on_text'] = stream
        try:
            result = self.inner.complete(purpose, messages, **kw)
            usage = result.usage
            entry.update(text=result.text, search_hits=[asdict(h) for h in result.search_hits],
                         citations=[asdict(h) for h in result.citations])
            row['status'] = 'received'
            return result
        except ModelError as exc:
            usage = exc.usage
            entry.update(error_code=exc.code, finish_reason=exc.finish_reason,
                         generated_text_length=exc.generated_text_length)
            row.update(status='failed', error_code=exc.code)
            raise
        finally:
            entry.update(seconds=round(time.monotonic()-started, 4), first_text_seconds=first[0] if first else None)
            if usage is not None:
                normalized = ce.gateway_module.ModelGateway._billable_usage(usage, self.config)
                entry['usage'] = asdict(normalized)
                cost = self.inner.price_catalog.cost(self.config['provider_id'], self.config['model'], normalized, at=row['at'])
                peak = self.inner.price_catalog.cost(self.config['provider_id'], self.config['model'], normalized, at=row['at'], context=context)
                row.update(catalogue_cost=str(cost.amount) if cost else None, peak_cost=str(peak.amount) if peak else None,
                           input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                           cached_input_tokens=usage.cached_input_tokens)
                entry['cost'] = asdict(cost) if cost else None
            with self.lock:
                entry.update(status=row['status'], billing=copy.deepcopy(row))
                self.calls.append(entry)
                ce.dump(self.output/'calls'/(identifier+'.json'), entry)
                ce.dump(self.budget_path, self.rows)

    def spent(self):
        with self.lock:
            # Settled usage uses the catalogue's timestamp price (including
            # off-peak). Unreported usage and in-flight calls keep peak reserves.
            return sum(Decimal(r.get('catalogue_cost') or r.get('peak_cost') or r['reserved']) for r in self.rows)


class HistoryRun(base.Run):
    def __init__(self, stack, phase):
        base.OUT, base.ACCOUNT = OUT, 'acceptance_history_24'
        base.PERSONA = '零基础的中学生，学习唐朝与丝绸之路；认真复习材料，用自己的话回答。'
        stack.enter_context(patch.object(ce, 'RecordedGateway', HistoryGateway))
        super().__init__(stack, phase)
        self.gw = self.gw.inner  # real preparation stays concurrent; reservations are atomic
        self.gw.phase = phase
        self.services.gateway = self.gw
        self.faults = json.loads((OUT/'reopens.json').read_text(encoding='utf-8')) if (OUT/'reopens.json').exists() else []
        self.coverage = json.loads((OUT/'coverage.json').read_text(encoding='utf-8')) if (OUT/'coverage.json').exists() else {}
        self.room._rng = random.Random(2406)

    def goto(self, offset, hour=12):
        super().goto(offset, hour)
        self.gw.study_date = self.day().isoformat()

    def act(self, name, *args, **kwargs):
        self.gw.study_date = self.day().isoformat()
        before = copy.deepcopy(self.room._load(self.room._course(self.plan_id)))
        # Simulate power loss at a persisted judgement / closed-attempt boundary.
        # Restoring the pre-operation room file reproduces the two-store window;
        # the database is never rewound and recovery uses the real implementation.
        pending = before.get('pending') or {}
        label = None
        if self.phase == 2 and name == 'reply':
            if pending.get('kind') == 'casual' and not self.coverage.get('judgement_crash'):
                label = '判定后、老师回复前'
            test = before.get('test') or {}
            if test and test.get('index') == len(test.get('queue', []))-1 and not self.coverage.get('close_crash'):
                label = '模块检测最后一题入库后、课堂文件落盘前'
        original = self.gw.complete
        count = len(self.store.records('judgements', self.plan_id))
        def fault(purpose, messages, **kw):
            if purpose == 'teacher' and label and len(self.store.records('judgements', self.plan_id)) > count:
                raise InterruptedError('acceptance simulated power loss before teacher request')
            return original(purpose, messages, **kw)
        try:
            with patch.object(self.gw, 'complete', fault):
                v = super().act(name, *args, **kwargs)
        except InterruptedError:
            self.room._save(self.room._course(self.plan_id),before)
            self.reopen(label)
            self.coverage['close_crash' if label.startswith('模块') else 'judgement_crash'] = True
            ce.dump(OUT/'coverage.json',self.coverage)
            v = super().act(name, *args, **kwargs)
            new_count = len(self.store.records('judgements', self.plan_id))
            self.faults[-1]['judgements_after_replay'] = new_count
            self.faults[-1]['same_answer_event_once'] = sum(j['id']==pending.get('answer_id') for j in self.store.records('judgements', self.plan_id)) == 1
            ce.dump(OUT/'reopens.json', self.faults)
        # Keep exact question/reference/reply/state next to the observed verdict.
        ce.dump(OUT/'operations'/f'op-{len(self.ops):04d}.json', dict(operation=self.ops[-1], before=before,
            after=self.room._load(self.room._course(self.plan_id)), view=v))
        errors = self.ops[-1].get('errors', [])
        if errors:
            self.note('课堂错误：' + '；'.join(errors))
        return v

    def create(self):
        file = OUT/'plan-id.txt'
        if file.exists():
            self.plan_id = file.read_text(encoding='utf-8').strip()
            if self.store.get_plan(self.plan_id)['state'] != 'creating': return
            draft = self.flow.load(self.plan_id)
        else:
            draft = self.flow.current()
            self.plan_id = draft['plan_id']
            file.write_text(self.plan_id, encoding='utf-8')
        if draft['phase'] == 'form':
            raw = dict(goal=GOAL, final_goal='能在地图上解释路线和主要节点，并用史实说明唐代对外交流、商品和文化传播。',
                level='beginner', start=base.START.isoformat(), end=(base.START+timedelta(days=9)).isoformat(),
                daily_cap=60, weekday_weights=[1]*7, special_days=[], only_mine=False,
                notes='零基础，中学历史水平；只学这三个范围，不扩展到丝路全部历史。预计8–10天，先给出时长预估。')
            draft = self.plan_step('填表、需求核对、联网搜索及评分', lambda d,c:self.flow.submit_form(d,c,raw))
        if draft['phase'] == 'questions':
            draft = self.plan_step('需求补充', lambda d,c:self.flow.chat(d,c,'按中学历史水平，掌握长安—天山廊道路线节点、唐代交流、商品文化传播，不需要背全部遗址年代。每天最多60分钟，10天。'))
        if draft['phase'] == 'sources':
            for card in list(draft['sources']):
                if card['status'] == 'new':
                    draft = self.plan_step('采用检索权威资料', lambda d,c,k=card['card_id']:self.flow.decide(d,k,True))
            adopted = [s for s in draft['sources'] if s['status']=='adopted']
            if len(adopted) < 3:
                self.coverage['pasted_unesco'] = True
                draft = self.plan_step('权威资料不足，贴入指定 UNESCO 网址', lambda d,c:self.flow.more_sources(d,c,FALLBACK))
            else: self.coverage['pasted_unesco'] = False
            ce.dump(OUT/'research.json', dict(sources=draft['sources'], research=draft['research'], messages=draft['messages']))
            draft = self.plan_step('路线轮廓', lambda d,c:self.flow.make_outline(d,c))
        if draft['phase']=='outline':
            ce.dump(OUT/'outline.json', draft['outline'])
            ranges = draft['outline']['ranges']
            self.note('用户可见时长预估：'+json.dumps(ranges,ensure_ascii=False))
            self.plan_step('正式生成前 token 预估', lambda d,c:self.flow.estimate(d))
        if draft['phase'] in ('outline','generating'):
            draft = self.plan_step('正式生成', lambda d,c:self.flow.generate(d,c))
        if draft['phase']!='ready':
            raise RuntimeError('无法开始计划，当前阶段：'+draft['phase'])
        self.plan_step('开始计划', lambda d,c:self.flow.start(d))
        ce.dump(OUT/'generated.json', dict(plan=self.store.get_plan(self.plan_id),
            modules=self.store.records('modules',self.plan_id), points=self.store.records('points',self.plan_id)))
        ce.dump(OUT/'sources.json',dict(sources=self.store.records('sources',self.plan_id), chunks=self.store.records('source_chunks',self.plan_id)))

    def student(self, mode):
        state = self.room._load(self.room._course(self.plan_id))
        pending = state.get('pending') or {}
        extra = {
            'paraphrase':'完整回答，但采用同义表达，不照抄教材或题目。',
            'vague':'回答模糊，只有大方向，少一个必要关系或条件，不直接说不会。',
            'insist_wrong':'给出一个明显事实错误，末尾加“我肯定对”。',
            'not_understood':'只说“没听懂，能换个说法吗”，暂不作答。',
        }.get(mode,'')
        if (self.view.get('question') or {}).get('type') == 'choice':
            extra = '只输出所选选项的完整原文，不加字母、解释或引导语。'
        packet = dict(persona=base.PERSONA, mode='correct' if mode=='paraphrase' else mode,
            instruction=extra, question=self.view.get('question'),
            dialogue=[dict(role=m['role'],text=m['content']) for m in self.view['messages'][-6:]],
            textbook=self.chunks_for(pending.get('point_ids') or []))
        result = self.gw.complete('simulated_student',[dict(role='user',content=json.dumps(packet,ensure_ascii=False))],
            system=ce.STUDENT_SYSTEM+'\n遵循 instruction 指定的这次表达方式。',max_tokens=4096)
        return _json_object(result.text)['reply']

    def reopen(self, label):
        before = self.room._load(self.room._course(self.plan_id))
        n = len(self.store.records('judgements',self.plan_id))
        self.room = Classroom(self.services,self.day,now=self.room.now,rng=random.Random(2406))
        self.view = self.room.run(self.plan_id,lambda *a:None)
        after = self.room._load(self.room._course(self.plan_id))
        self.faults.append(dict(label=label,before=before,after=after,judgements_before=n,
            judgements_after=len(self.store.records('judgements',self.plan_id)),step=self.view['step']))
        ce.dump(OUT/'reopens.json',self.faults)

    def session(self, *, stop, modes, test_wrong_once=False, force_one_test=False, use_direct_test=False):
        self.act('enter')
        test_started=False
        same=0; prior=None
        for _ in range(180):
            step=self.view['step']
            if (step not in ('testing','retesting','preparing_questions')
                    and not self.view.get('red') and stop(self.today())
                    and (not force_one_test or test_started)):
                break
            pending=self.room._load(self.room._course(self.plan_id)).get('pending')
            key=json.dumps([step,pending],sort_keys=True,ensure_ascii=False)
            same=same+1 if key==prior else 0; prior=key
            if same>=3 and self.ops[-1].get('errors'):
                raise RuntimeError('流程阻断：同一状态连续失败，停止付费请求')
            if step=='completed':break
            if step=='preparing_questions':
                self.view=self.room.run(self.plan_id,lambda *a:None,self.ctx())
                for t in threading.enumerate():
                    if t.name.startswith('classroom-'):t.join(120)
                continue
            if step=='teach':
                if stop(self.today()) and (not force_one_test or test_started):break
                if use_direct_test and self.view.get('can_skip') and not self.coverage.get('direct_test'):
                    self.coverage['direct_test']=True;self.act('start_test','skip_test')
                else:self.act('next')
            elif step=='answer':
                mode=next(modes,'correct')
                text=self.student(mode)
                self.coverage[mode]=self.coverage.get(mode,0)+1
                self.act('reply',text)
            elif step=='test_offer':
                if stop(self.today()) and (not force_one_test or test_started):break
                self.act('start_test','module_test');test_started=True
            elif step in ('testing','retesting'):
                composite = len((pending or {}).get('point_ids', [])) > 1
                flag = 'composite_wrong' if composite else 'single_test_wrong'
                mode='wrong' if test_wrong_once and step=='testing' and not self.coverage.get(flag) else 'paraphrase'
                if mode=='wrong':self.coverage[flag]=True
                self.act('reply',self.student(mode))
                if self.view['step']=='test_done' and not self.coverage.get('module_reopen'):
                    self.reopen('模块检测最后一题后');self.coverage['module_reopen']=True
            elif step=='test_done':
                if self.view.get('red'):self.act('start_test','retest')
                elif force_one_test and test_started and stop(self.today()):break
                else:self.act('next_module')
            elif step=='check_offer':self.act('start_check')
            elif step=='checking':
                if self.view.get('question'):
                    mode='wrong' if not self.coverage.get('final_wrong') else 'paraphrase'
                    self.coverage['final_wrong']=True
                    self.act('reply',self.student(mode))
                else:self.act('start_check')
            else:raise RuntimeError('未知课堂阶段：'+step)
            ce.dump(OUT/'coverage.json',self.coverage)
            if self.gw.spent()>=(Decimal('2.4') if self.phase==1 else Decimal('4.9')):
                raise RuntimeError('预算接近上限；停止推进')
        self.act('leave')

    def script(self):
        met=lambda t:t['today_target'] and t['today_confirmed']+t['today_tentative']>=t['today_target']
        if self.phase==1:
            if (OUT/'phase1-complete.flag').exists():raise RuntimeError('第一段已跑，禁止重复付费')
            self.create()
            self.goto(0);self.record_day('D1 上课前')
            self.session(stop=met,modes=iter(['question','chat','not_understood','wrong','paraphrase','vague','paraphrase','insist_wrong','correct']))
            self.record_day('D1 下课')
            self.goto(1);self.record_day('D2 上课前')
            self.session(stop=lambda t:t['today_target'] and t['today_confirmed']+t['today_tentative']>=2*t['today_target'],
                modes=iter(['paraphrase']),test_wrong_once=True,force_one_test=True)
            self.record_day('D2 多学与模块检测')
            self.goto(2);self.record_day('D3 不上课')
            self.goto(3);self.record_day('D4 上课前')
            before=self.today()['remaining_minutes']
            self.session(stop=lambda t:t['remaining_minutes']<before,modes=iter(['paraphrase']))
            self.record_day('D4 少学')
            (OUT/'phase1-complete.flag').write_text('completed',encoding='utf-8')
        else:
            if not (OUT/'phase1-audited.flag').exists():raise RuntimeError('需先人工核对第一段，确认无全流程阻断')
            self.plan_id=(OUT/'plan-id.txt').read_text(encoding='utf-8').strip()
            if not self.coverage.get('cap_selected_from_estimate'):
                self.goto(0)
                # Initial 60 was provisional; choose 80 from the observed
                # upper estimate (73/day), keeping the same ten-day window.
                self.flow.change_window(self.plan_id, {'daily_cap':80})
                self.coverage['cap_selected_from_estimate']=80
                self.note('依据轮廓上限73分钟/天，选择每日上限80分钟；日期不变。')
            # A budget stop can happen after the first completed module but
            # before all early-day scenarios. Continue its state, never rebuild
            # the plan or repeat already completed days.
            if not any(d['label']=='D1 下课' for d in self.days):
                self.goto(0)
                self.session(stop=met,modes=iter(['paraphrase']))
                self.record_day('D1 下课')
            if not any(d['label']=='D2 多学与模块检测' for d in self.days):
                self.goto(1);self.record_day('D2 上课前（续跑）')
                self.session(stop=lambda t:t['today_target'] and t['today_confirmed']+t['today_tentative']>=2*t['today_target'],
                    modes=iter(['paraphrase']),test_wrong_once=True,force_one_test=True)
                self.record_day('D2 多学与模块检测')
            if not any(d['label']=='D3 不上课' for d in self.days):
                self.goto(2);self.record_day('D3 不上课')
            if not any(d['label']=='D4 少学' for d in self.days):
                self.goto(3);self.record_day('D4 上课前')
                before=self.today()['remaining_minutes']
                self.session(stop=lambda t:t['remaining_minutes']<before,modes=iter(['paraphrase']))
                self.record_day('D4 少学')
            for offset in range(4,22):
                self.goto(offset);self.record_day(f'D{offset+1} 上课前')
                before=self.today()['remaining_minutes']
                self.session(stop=(lambda t:t['remaining_minutes']<before) if offset==4 else (lambda t:False),
                    modes=iter(['paraphrase']),use_direct_test=offset>4,test_wrong_once=True)
                self.record_day(f'D{offset+1} 下课')
                if self.store.get_plan(self.plan_id)['state']=='completed':break
            if self.store.get_plan(self.plan_id)['state']=='completed':self.complete_and_practice()

    def dump(self):
        super().dump()
        if self.plan_id:
            for kind in ('sources','source_chunks','question_bank','judgements','credits','ledger_events','attempt_closed','practice_results'):
                ce.dump(OUT/(kind+'.json'),self.store.records(kind,self.plan_id))
            # Database message insertion ordering, avoiding random-id order when
            # several operations share a simulated timestamp.
            records=self.store.records('messages',self.plan_id)
            lines=['# 唐朝与丝绸之路：完整对话','']
            for m in sorted(records,key=lambda m:(m.get('seq',0),m['at'])):
                lines += [f"### {m['id']} · {m['role']} · {m.get('kind')} · {m.get('module_id')}",'',m['content'],'']
            (OUT/'dialogue.md').write_text('\n'.join(lines),encoding='utf-8')
        ce.dump(OUT/'coverage.json',self.coverage)


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8',errors='replace')
    if os.environ.get('MF_HISTORY_REAL')!='1':raise RuntimeError('真实验收需 MF_HISTORY_REAL=1')
    phase=int(os.environ.get('MF_HISTORY_PHASE','1'))
    if phase not in (1,2):raise RuntimeError('阶段只能为1或2')
    with ExitStack() as stack:
        run=HistoryRun(stack,phase)
        try:run.script()
        finally:run.dump()


def test_history_acceptance_explicit_opt_in():
    import pytest
    if os.environ.get('MF_HISTORY_REAL')!='1':pytest.skip('history real-model acceptance is opt-in')
    main()


def test_history_budget_refuses_request_before_network(tmp_path):
    from types import SimpleNamespace
    import pytest
    client=SimpleNamespace(get_configuration=lambda:dict(provider_id='deepseek',model='deepseek-flash'),
        estimate_context=lambda:{},price_catalog=SimpleNamespace(estimate_cost=lambda *a,**k:SimpleNamespace(high=Decimal('0.1'),currency='CNY')))
    recorder=HistoryGateway(client,tmp_path,budget_path=tmp_path/'budget.json')
    recorder.rows=[dict(reserved='2.49')]
    with pytest.raises(ModelError,match='预算上限') as error:
        recorder.complete('teacher',[dict(role='user',content='offline')])
    assert error.value.code=='EVAL_BUDGET_STOP'
    assert not (tmp_path/'calls').exists()


def test_history_recorder_keeps_real_search_flag_and_sources(tmp_path):
    from types import SimpleNamespace
    from refactor.learning.connections.providers import ModelResult,Usage,WebHit
    received=[]
    def complete(purpose,messages,**kw):
        pending = json.loads((tmp_path/'calls/call-0001.json').read_text(encoding='utf-8'))
        assert pending['status'] == 'pending'
        assert pending['messages'] == messages
        received.append(kw)
        return ModelResult('result',Usage(10,0,10,1,0,1,{}),'deepseek','deepseek-flash',
                           ('history',),(WebHit('https://example.org','source','excerpt'),),(),'end_turn')
    client=SimpleNamespace(get_configuration=lambda:dict(provider_id='deepseek',model='deepseek-flash',base_url='https://api.deepseek.com/anthropic'),
        estimate_context=lambda:{},complete=complete,
        price_catalog=SimpleNamespace(estimate_cost=lambda *a,**k:SimpleNamespace(high=Decimal('0.1'),currency='CNY'),cost=lambda *a,**k:None))
    recorder=HistoryGateway(client,tmp_path,budget_path=tmp_path/'budget.json')
    recorder.complete('research',[dict(role='user',content='offline')],enable_search=True)
    entry=json.loads((tmp_path/'calls/call-0001.json').read_text(encoding='utf-8'))
    assert received[0]['enable_search'] is True
    assert entry['search_hits'][0]['snippet']=='excerpt'
    assert recorder.spent()==Decimal('0.1')  # unknown usage price retains reserve


if __name__=='__main__':main()
