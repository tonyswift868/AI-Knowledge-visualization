"""Real AppServices/AppBridge, injected domain clock, trace and budget fences."""
from contextlib import ExitStack
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, time as daytime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from refactor.learning.connections.errors import ModelError

SCRATCH=Path(r'D:\MentorFlow\.local\test-tmp')


class StopRun(RuntimeError):
    def __init__(self,code,message): self.code=code; super().__init__(message)


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


class Clock:
    def __init__(self,value='2026-10-03T10:00:00+09:00'):
        self.value=datetime.fromisoformat(value)
        if self.value.tzinfo is None: raise ValueError('模拟时钟必须含时区')
    def now(self): return self.value
    def day(self,settings=None):
        from refactor.learning.course.ledger import study_day
        h,m=map(int,(settings or {}).get('refresh_time','04:00').split(':'))
        return study_day(self.value,daytime(h,m),self.value.tzinfo)
    def advance(self,days=1,minutes=0):
        if days<0 or minutes<0: raise ValueError('不回拨模拟时钟')
        self.value+=timedelta(days=days,minutes=minutes)


class Evidence:
    def __init__(self,root):
        self.root=Path(root).resolve()
        if not self.root.is_relative_to(SCRATCH.resolve()): raise ValueError('运行目录须在D盘test-tmp下')
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.started=time.monotonic();self.stopped=None
        self.calls=[];self.steps=[];self.issues=[]
        for name,default in [('calls',[]),('steps',[]),('issues',[])]:
            p=self.root/(name+'.json')
            if p.exists():setattr(self,name,json.loads(p.read_text(encoding='utf-8')))
    def event(self,kind,**data):
        with self.lock:
            with (self.root/'events.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(kind=kind,wall_utc=datetime.now(timezone.utc).isoformat(),**data),ensure_ascii=False,default=str)+'\n')
    def save(self,name,value): dump(self.root/name,value)
    def issue(self,code,message,evidence=None):
        with self.lock:
            item=dict(code=code,message=message,evidence=evidence);self.issues.append(item);self.save('issues.json',self.issues)
    def summary(self,stage,cost):
        live=[c for c in self.calls if not c['offline']]
        unpriced=sum('time_cost_cny' not in c for c in live)
        time_cost=sum((Decimal(c.get('time_cost_cny','0')) for c in live),Decimal(0))
        value=dict(stage=stage,stop=self.stopped,steps=len(self.steps),calls=len(self.calls),cost_cny=str(cost),
            time_cost_cny=str(time_cost) if not unpriced else None,unpriced_calls=unpriced,
            seconds=round(time.monotonic()-self.started,3),issues=self.issues,
            quality='不自动评分；请人工查看原始请求、屏幕、原文与作答',evidence=dict(steps='steps/',calls='model/',events='events.jsonl'))
        self.save('summary.json',value)
        (self.root/'summary.md').write_text(f'# 模拟运行摘要\n\n走到：{stage}\n\n停止：{self.stopped or "按段完成"}\n\n调用：{len(self.calls)}；花费：¥{cost}\n\n质量由人核对，不自动评分。\n\n'+
            '\n'.join(f'- {i["code"]}: {i["message"]}；证据 {i["evidence"]}' for i in self.issues),encoding='utf-8')
        return value


class RecordedGateway:
    """One shared budget for host and user, including background/failed calls."""
    def __init__(self,real,evidence,*,budget='0',offline=False,allowed=None):
        self.real,self.evidence,self.limit,self.offline=real,evidence,Decimal(str(budget)),offline
        if not self.limit.is_finite() or self.limit<0:raise ValueError('预算必须是非负有限人民币金额')
        self.allowed=allowed;self.lock=threading.RLock();self.halted=threading.Event();self.protocol_retries=set()
        self.spent=sum((Decimal(c.get('charged_bound','0')) for c in evidence.calls),Decimal(0));self.reserved=Decimal(0)
        self.attempted=0;self.base_id=max((c['id'] for c in evidence.calls),default=0)
    def __getattr__(self,name): return getattr(self.real,name)
    def halt(self): self.halted.set()
    def reject(self,code,message,purpose):
        self.halt();self.evidence.event('model_not_started',purpose=purpose,code=code,message=message)
        self.evidence.issue(code,message,'events.jsonl')
        raise ModelError(code,message)
    def complete(self,purpose,messages,**kwargs):
        from refactor.learning.connections.gateway import PURPOSE_MIN_OUTPUT
        # A simulation-only purpose is traced separately; its non-thinking user
        # decision uses the existing plan_intent provider settings. No product edit.
        wire='plan_intent' if purpose=='simulation_user' else purpose
        if self.halted.is_set(): raise ModelError('SIMULATION_STOPPED','模拟已止损，禁止后续调用。')
        if self.allowed is not None and purpose not in self.allowed:
            self.reject('SIMULATION_SCOPE','本段不允许调用用途：'+purpose,purpose)
        amount=Decimal(0);config=self.real.get_configuration() or {}
        real_now=datetime.now(timezone.utc)  # Billing is NEVER the simulated date.
        tokens=max(kwargs.get('max_tokens',4096),PURPOSE_MIN_OUTPUT.get(wire,0))
        if not self.offline:
            context=dict(self.real.estimate_context(),price_period='peak',cache_mode='implicit')
            upper=len((kwargs.get('system','')+json.dumps(messages,ensure_ascii=False)).encode('utf-8'))+4096
            estimate=self.real.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(upper,upper),
                output_tokens=(tokens,tokens),cache_hit_rate=(0,0),context=context)
            if estimate is None or estimate.currency!='CNY':
                self.reject('SIMULATION_UNPRICED','无法确认人民币价格，不进行付费调用。',purpose)
            amount=estimate.high
        with self.lock:
            if self.halted.is_set() or (not self.offline and self.spent+self.reserved+amount>self.limit):
                self.reject('SIMULATION_BUDGET','下一次调用预留会超过花费上限，已停止。',purpose)
            self.reserved+=amount;self.attempted+=1
            cid=self.base_id+self.attempted
        started=time.monotonic();result=None;error=None;usage=None
        record=dict(id=cid,purpose=purpose,wire_purpose=wire,wall_utc=real_now.isoformat(),reservation_cny=str(amount),
            system=kwargs.get('system'),messages=messages,plan_id=kwargs.get('plan_id',''))
        # Persist request before network; failed calls remain reconstructible.
        file=f'model/call-{cid:05d}.json';self.evidence.save(file,record)
        try:
            result=self.real.complete(wire,messages,**kwargs);usage=getattr(result,'usage',None)
            record.update(text=result.text,search_hits=[asdict(h) if is_dataclass(h) else str(h) for h in getattr(result,'search_hits',())])
            return result
        except Exception as exc:
            error=exc;usage=getattr(exc,'usage',None)
            record['error']=dict(code=getattr(exc,'code',type(exc).__name__),message=str(exc))
            # Let the host execute its existing ONE malformed-protocol retry.
            # The recorder must not replace that retry with SIMULATION_STOPPED.
            # It never retries itself; transport failures still stop immediately.
            key=json.dumps([purpose,messages,kwargs.get('system'),kwargs.get('plan_id'),
                            kwargs.get('enable_search',False)],sort_keys=True,ensure_ascii=False)
            with self.lock:
                allow=(record['error']['code']=='MODEL_RESPONSE_INVALID' and purpose!='simulation_user'
                       and key not in self.protocol_retries)
                # Module generation owns its bounded cap retry and split into
                # halves (BUG-15). Do not halt before that host path executes;
                # every attempt still reserves/charges the same shared budget.
                truncation=(record['error']['code']=='MODEL_OUTPUT_TRUNCATED' and purpose=='generate')
                if allow:self.protocol_retries.add(key)
                elif not truncation:self.halt()
            record['host_protocol_retry_allowed']=allow
            if truncation:record['host_batch_split_allowed']=True
            if isinstance(getattr(exc,'diagnostics',None),dict):record['diagnostics']=exc.diagnostics
            raise
        finally:
            record['seconds']=round(time.monotonic()-started,3)
            charged=actual=Decimal(0)
            if not self.offline:
                charged=amount  # Unknown usage retains the reservation, not zero.
                if usage is not None:
                    record['usage']=asdict(usage) if is_dataclass(usage) else str(usage)
                    for peak,name in [(True,'peak_cost_cny'),(False,'time_cost_cny')]:
                        ctx=dict(self.real.estimate_context(),cache_mode='implicit')
                        if peak:ctx['price_period']='peak'
                        cost=self.real.price_catalog.cost(config['provider_id'],config['model'],usage,at=real_now,context=ctx)
                        if cost is not None:
                            record[name]=str(cost.amount)
                            if peak:charged=cost.amount
                            else:actual=cost.amount
            record.update(charged_bound=str(charged),offline=self.offline,evidence=file)
            with self.lock:
                self.reserved-=amount;self.spent+=charged
                self.evidence.calls.append({k:v for k,v in record.items() if k not in ('messages','system','text','usage','search_hits')})
                self.evidence.save(file,record);self.evidence.save('calls.json',self.evidence.calls)
                if error:self.evidence.issue(record['error']['code'],str(error),file)
                if not self.offline and self.spent>=self.limit:self.halt()
    def close(self): self.halt();self.real.close()


class Simulation:
    """A headless Qt event pump, real registrations, account-isolated services."""
    def __init__(self,root,clock,gateway_factory,*,account='simulation42',fetcher=None,budget='0',offline=False,timeout=180,allowed=None):
        self.evidence=Evidence(root);self.clock=clock;self.timeout=timeout;self.stack=ExitStack();self.closed=False
        self.account,self.fetcher,self.gateway_factory=account,fetcher,gateway_factory
        self.budget,self.offline,self.allowed=budget,offline,allowed
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication,QFileDialog
        from refactor.learning.app import classroom,handlers
        from refactor.learning.course import store,point_materials
        self.qapp=QApplication.instance() or QApplication([])
        original_room=classroom.Classroom;original_at=store._at
        def room(*args,**kwargs):
            kwargs['now']=clock.now;kwargs['rng']=random.Random(42)
            return original_room(*args,**kwargs)
        class DomainDateTime(datetime):
            @classmethod
            def now(cls,tz=None):return clock.now().astimezone(tz) if tz else clock.now().replace(tzinfo=None)
        self.stack.enter_context(patch.object(classroom,'Classroom',room))
        self.stack.enter_context(patch.object(handlers,'_local_study_day',clock.day))
        self.stack.enter_context(patch.object(handlers,'datetime',DomainDateTime))
        self.stack.enter_context(patch.object(store,'_at',lambda value=None:original_at(clock.now() if value is None else value)))
        self.stack.enter_context(patch.object(point_materials,'datetime',DomainDateTime))
        # The UI picker is replaced only with the user-selected isolated save
        # path. Rendering itself and the plan.report_pdf handler remain real.
        def save_dialog(*args,**kwargs):
            suffix='.pdf' if 'PDF' in str(args[1:]) else '.md'
            return str(self.evidence.root/('learning-report'+suffix)), ''
        self.stack.enter_context(patch.object(QFileDialog,'getSaveFileName',save_dialog))
        self.upload_path=None
        self.stack.enter_context(patch.object(QFileDialog,'getOpenFileNames',lambda *a,**k:([str(self.upload_path)] if self.upload_path else [],'')))
        try:self.bind()
        except Exception as exc:
            self.evidence.stopped=dict(code='INIT_FAILED',message=str(exc))
            self.evidence.issue('INIT_FAILED',str(exc),'summary.json');self.evidence.summary('form',Decimal(0))
            if hasattr(self,'services'):self.services.close()
            self.stack.close();raise
    def bind(self):
        from refactor.learning.app.services import AppServices
        from refactor.learning.app.bridge import AppBridge
        from refactor.learning.app import classroom,plan_flow
        from refactor.learning.course.source_filter import load_lists
        # Disable enterprise catalog refresh; built-ins are read locally. No
        # business server/token is touched, even in a live-model simulation.
        self.services=AppServices(account_id=self.account,data_root=self.evidence.root/'data',session_provider=lambda:None,
            api_base_url='https://simulation.invalid',ca_bundle=None)
        self.services._catalog=SimpleNamespace(load_lists=load_lists,load_prices=lambda:json.loads(
            (Path(__file__).resolve().parents[3]/'refactor/learning/course/data/model-prices.json').read_text(encoding='utf-8')),
            close=lambda:None)
        real=self.gateway_factory(self.services)
        self.gateway=RecordedGateway(real,self.evidence,budget=self.budget,offline=self.offline,allowed=self.allowed)
        self.services._gateway=self.gateway
        self.bridge=AppBridge();self.received=[]
        self.bridge.event.connect(self.receive)
        window=SimpleNamespace(services=self.services)
        plan_flow.register(self.bridge,lambda:self.services,window,self.clock.day,fetcher=self.fetcher)
        classroom.register(self.bridge,lambda:self.services,lambda:self.clock.day(self.services.settings.load()))
    def receive(self,text):
        event=json.loads(text);self.received.append(event);self.evidence.event('bridge',payload=event)
    def call(self,method,**params):
        if self.gateway.halted.is_set():raise StopRun('STOPPED','调用已止损')
        sequence=len(self.evidence.steps)+1;rid=f'sim-{sequence}-{time.monotonic_ns()}'
        request=dict(id=rid,method=method,params=params)
        path=f'steps/step-{sequence:05d}.json';record=dict(request=request,simulated_now=self.clock.now().isoformat(),study_day=self.clock.day().isoformat())
        self.evidence.save(path,record);start=time.monotonic();self.bridge.call(json.dumps(request,ensure_ascii=False))
        while True:
            self.qapp.processEvents()
            reply=next((v for v in self.received if v.get('id')==rid and v['type']=='response'),None)
            if reply:break
            if time.monotonic()-start>self.timeout:
                self.bridge.cancel_pending();self.gateway.halt()
                self.evidence.issue('STEP_TIMEOUT',method,path)
                raise StopRun('STEP_TIMEOUT',method)
            time.sleep(.005)
        record.update(response=reply,seconds=round(time.monotonic()-start,3),cost_bound=str(self.gateway.spent))
        self.evidence.save(path,record);self.evidence.steps.append(dict(method=method,evidence=path,seconds=record['seconds']))
        self.evidence.save('steps.json',self.evidence.steps)
        result=reply.get('result',{});failure=result.get('failure') or (result.get('draft') or {}).get('error')
        # Reading a recovered draft can legitimately show the previous run's
        # saved error. Keep it as evidence, but do not treat it as a new failure
        # of this read-only bridge request and prevent a user-initiated retry.
        errors=[] if method in ('plan.get','plan.current','plan.drafts') else [
            m for m in (result.get('draft') or {}).get('messages',[])[-1:] if m.get('kind')=='error']
        if not reply['ok'] or failure or errors or self.gateway.halted.is_set():
            detail=reply.get('error') or failure or errors or '模型调用已止损'
            self.evidence.issue('BRIDGE_FAILED',str(detail),path);self.gateway.halt()
            raise StopRun('BRIDGE_FAILED',str(detail))
        return result
    def advance_day(self):
        self.clock.advance();self.evidence.event('advance_day',simulated_now=self.clock.now().isoformat())
    def join_jobs(self):
        # Only workers carrying this tool's callbacks / bridge pool; product
        # workers are joined before closing their store, avoiding SQLite locks.
        for thread in list(threading.enumerate()):
            if thread.name.startswith(('plan-materials','classroom-','plan-upload')):
                thread.join(min(self.timeout,30))
                if thread.is_alive():raise StopRun('WORKER_TIMEOUT',thread.name)
    def reopen(self,plan_id):
        self.call('classroom.leave',plan_id=plan_id);self.join_jobs()
        self.bridge.close();self.bridge._pool.shutdown(wait=True,cancel_futures=True);self.services.close()
        self.evidence.event('reopen',plan_id=plan_id);self.bind()
        return self.call('classroom.enter',plan_id=plan_id)
    def reopen_draft(self,plan_id):
        """Rebuild services and read the saved draft through the real UI bridge."""
        self.join_jobs()
        self.bridge.close();self.bridge._pool.shutdown(wait=True,cancel_futures=True);self.services.close()
        self.evidence.event('draft_reopen',plan_id=plan_id);self.bind()
        return self.call('plan.get',plan_id=plan_id)['draft']
    def close(self):
        if self.closed:return
        self.closed=True
        self.bridge.cancel_pending();self.gateway.halt()
        try:
            self.join_jobs()
            self.bridge.close();self.bridge._pool.shutdown(wait=True,cancel_futures=True);self.services.close()
        finally:self.stack.close()
