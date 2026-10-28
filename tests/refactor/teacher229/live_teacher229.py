"""Opt-in twelve-question BUG-183 test. Import run() only after offline green."""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import patch
from tests.refactor.teacher226 import live_compare226 as old
from tests.refactor.teacher228 import live_teacher228 as base
from refactor.learning.app.classroom import Classroom
from refactor.learning.course import teacher_agent
from refactor.learning.connections import gateway as gateway_module

ROOT = Path('D:/MentorFlow/.local/test-tmp/teacher229/live')
EXPECTATION = Path('D:/MentorFlow/.local/test-tmp/teacher229/expectation.md')
OUTPUT = ROOT / 'classroom_generation229.json'
BUDGET_CNY = Decimal('5')

def _within_budget(measured,reserved,current,limit=BUDGET_CNY):
    return measured+reserved+current<=limit

def _committed_outlay(calls):
    return sum((Decimal(c['cost_cny']) if c.get('cost_cny') is not None
                else Decimal(c.get('reserved_retry_worst_case_cny','0')) for c in calls),Decimal(0))

def verify_budget_gate():
    assert _within_budget(Decimal('1.25'),Decimal('0'),Decimal('0.10'))
    assert not _within_budget(Decimal('4.98'),Decimal('0'),Decimal('0.03'))
    # A priced successful call releases its worst-case reservation; an
    # unpriced failure retains it, and neither may reset earlier spend.
    outlay=_committed_outlay([
      {'cost_cny':'0.12','reserved_retry_worst_case_cny':'0.80'},
      {'cost_cny':None,'reserved_retry_worst_case_cny':'0.55'}])
    assert outlay==Decimal('0.67')
    assert not _within_budget(Decimal('1.00'),outlay,Decimal('3.34'))
    return '4 budget cases passed; priced calls release reservation, unpriced calls retain it, and cumulative spend is guarded'
NEW = (
 ('material_business_followup','数据库约束在业务校验这套分工里承担什么作用？','architecture-note'),
 ('material_consistency_followup','最终一致性在什么条件下会收敛到一致状态？','consistency-note'),
 ('partial_business_trigger','资料说了业务层和数据库的职责；它有没有具体说明数据库触发器为什么不适合放流程规则？','architecture-note'),
 ('partial_balance_choice','这份强一致性资料能否据此断定余额业务必须采用强一致性？','consistency-note'),
 ('no_material_cpu_cache','CPU 缓存一致性是什么？',None),
 ('analogy_kitchen','能用做饭的厨房和书房比喻模块边界吗？','current'),
)
SAMPLES=tuple(old.SAMPLES)+NEW

class _BudgetGateway(base._CaptureGateway):
    def __init__(self,gateway,persist,*,committed_before=Decimal(0)):
        super().__init__(gateway,persist)
        self.committed_before=committed_before

    def complete(self,purpose,messages,*,system=None,**kwargs):
        # Completed requests release their reservation into measured peak cost;
        # unpriced or transport-failed calls keep their worst-case reservation.
        bound=old._estimate(self.gateway,system or '',messages)
        committed=self.committed_before+_committed_outlay(self.calls)
        if not _within_budget(committed,Decimal(0),bound):
            raise base._BudgetExceeded('¥5 cumulative measured-plus-reserved budget gate')
        # The 228 adapter checks its module constant, so temporarily set that
        # guard to the original cumulative worst-case reservation plus this
        # outbound request after our stricter measured-cost guard has passed.
        prior=base.BUDGET_CNY
        base.BUDGET_CNY=self.reserved+bound
        try:
            return super().complete(purpose,messages,system=system,**kwargs)
        finally:
            base.BUDGET_CNY=prior

def _context(sample):
    if len(sample)==4:
        return old._context_for(sample,current=True)
    _,question,key=sample
    source={
      'architecture-note':'合成讲义：业务规则校验由应用服务层负责；数据库约束作为最后一道完整性防线。',
      'consistency-note':'合成讲义：强一致性要求写入完成后后续读取立即看到新值；最终一致性允许短暂延迟，但系统会在没有新写入时收敛到一致状态。',
      'current':'合成讲义：模块边界用于把系统拆成职责清楚、可以独立验证的部分。',
    }
    return old._make_context(question,{'current':'当前点只介绍模块边界。'},
      ({key:source[key]} if key else {}),current=True)

def run(*,samples=SAMPLES,output=OUTPUT,committed_before=Decimal(0),expectation=EXPECTATION):
    if Path.cwd().resolve()!=base.REPO.resolve(): raise RuntimeError(f'Run from {base.REPO}')
    if not expectation.is_file() or not expectation.read_text(encoding='utf-8').strip():
        raise RuntimeError('Expectation/offline-green gate missing')
    if output.exists(): raise RuntimeError(f'Refuse overwrite: {output}')
    ROOT.mkdir(parents=True,exist_ok=True)
    real=base.live_factory(base.PRESET_ROOT,base.ACCOUNT)(SimpleNamespace(data_root=ROOT/'gateway-data'))
    try:
        config=real.get_configuration()
        if config['provider_id']!='deepseek' or config['model'] not in ('deepseek-v4-flash','deepseek-flash'):
            raise RuntimeError('Preset is not DeepSeek Flash; no request sent')
        report={'mode':'twelve_synthetic_questions_classroom_generation' if len(samples)>1 else 'single_question_recheck',
          'provider':config['provider_id'],'quality_self_evaluation':False,
          'model':config['model'],'expectation':expectation.read_text(encoding='utf-8'),
          'calls':[],'answers':[]}
        report['prior_task_actual_peak_price_cny']=str(committed_before)
        capture=_BudgetGateway(real,lambda:base._write(output,report),committed_before=committed_before)
        room=base._classroom_instance(capture); report['calls']=capture.calls; report['answers']=answers=[]
        base._write(output,report)
        with patch.dict(gateway_module.MODEL_MAX_OUTPUT,{config['model']:base.OUTPUT_CAP}):
            for sample in samples:
                sid,question=sample[0],sample[1]; ctx=_context(sample)
                system,messages=teacher_agent.teacher_request(ctx); stream=[]; start=len(capture.calls)
                item={'sample_id':sid,'question':question}
                try:
                    parsed,_,accepted=room._generate_teacher('teacher229-synthetic-plan',ctx,
                        (system,messages),threading.Event(),stream.append)
                    item.update(status='completed',basis=parsed.answer_basis,
                      cited_chunks=list(parsed.cited_chunks),speech=parsed.speech,
                      accepted_response=accepted,stream_events=stream)
                except base._BudgetExceeded as exc:
                    item.update(status='budget_stopped',error=str(exc)); answers.append(item)
                    base._write(output,report); break
                except Exception as exc:
                    item.update(status='generation_or_parse_error',error_type=type(exc).__name__,
                      error_code=getattr(exc,'reason',getattr(exc,'code',None)),error=str(exc),stream_events=stream)
                item['model_call_indexes']=list(range(start,len(capture.calls)))
                answers.append(item); base._write(output,report)
        report.update(call_count=len(capture.calls),reserved_retry_worst_case_cny=str(capture.reserved),
          actual_peak_price_cny=str(sum((Decimal(c['cost_cny']) for c in capture.calls if c.get('cost_cny')),Decimal(0))),
          unpriced_call_count=sum(c.get('cost_cny') is None for c in capture.calls),
          finished_at_utc=datetime.now(timezone.utc).isoformat())
        report['task_total_peak_price_cny']=str(committed_before+Decimal(report['actual_peak_price_cny']))
        base._write(output,report); return report
    finally: real.close()

if __name__=='__main__':
    import sys
    if sys.argv[1:]==['--budget-dry-check']:
        print(verify_budget_gate())
    else:
        raise SystemExit('No live request is sent by CLI; live runner must be called explicitly after offline green.')
