"""Grounded conversational classroom. Questions are selected, never generated."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import contextlib
import copy
import hashlib
import importlib
import json
import logging
import random
import re
import threading
import time
import uuid
from types import SimpleNamespace

from ..connections.errors import ModelCancelled, ModelError
from ..course import judge_agent, teacher_agent, question_bank, answer_compare
from ..course.request_layout import AppendOnlyPage, dialogue_tail
from ..course.mastery import AttemptClosed, Judgement, MasteryError
from ..course.planner_agents import LEVELS, AgentFormatError
from ..course.store import StoreError
from ..preferences.storage import AccountJSON, StorageError
from .bridge import BridgeError, CallContext

log = logging.getLogger(__name__)
_PLAN_ID = re.compile(r"[a-f0-9]{32}")
PREF_KEYS = ("teacher_style", "detail", "examples", "analogy_domains")
DIALOGUE_ROWS, DIALOGUE_CHARS, CHECK_BATCH = 40, 60000, 4
CONCEPT_WRAP_CONTEXT_CHARS = 8000
RESET, VERDICT_EVENT = "\x00", "\x01VERDICT:"
MATERIAL_EVENT = "\x01MATERIAL:"
TODAY_EVENT = "\x01TODAY:"
EXPLAIN = '顺着学生的开场回答讲本点重点。对的直接顺着讲，不重复铺垫；错了或不知道，不纠缠，一起看原文。每段简洁，不反复问清楚吗；不出计分题。'
CONFUSED = '学生没太懂，依据同一原文换一种口语说法或生活化例子；不能引入新知识结论，不出计分题。'
CONCEPT_START = '现在开始当前小要点。结合学生刚才的回应和最近的开场，自行选择生活化问题、短例子、对比或从上一小要点接过来，不按序号轮换套路，不连续套同一模板。不一次讲完全部小要点。围绕当前讲义展开，留空间让学生回应；不必每轮结尾问问题，不输出选项块。'
CONCEPT_WRAP = '这个大知识点的小要点已经讲完。用一两句、只说结论，把它们与掌握目标串起来收束，不再出问题，不重讲全文。'
PURPOSE = {"module_test": "module", "skip_test": "direct",
           "retest": "retest", "check": "final", "practice": "free"}


class _SupersededOperation(Exception):
    """A newer classroom action owns the position; this worker must not commit."""


def _now():
    return datetime.now(timezone.utc)


class Classroom:
    _mutex = threading.Lock()
    _room_locks = {}
    _active_operations = {}
    _operation_generations = {}

    def __init__(self, services, study_day, *, now=_now, rng=None):
        self.services, self.study_day, self.now = services, study_day, now
        self._rng = rng or random.Random()
        self._jobs, self._entered_plans = {}, set()
        self._local = threading.local()
        self.material_event = lambda data: None
        self._teacher_request_page = AppendOnlyPage()
        self._prefetch_lock = threading.RLock()
        self._prefetches = {}

    def cancel_materials(self, plan_id=None):
        """Only this classroom's preparation jobs, never the shared gateway/store."""
        for key,job in list(self._jobs.items()):
            if plan_id is None or key[0]==plan_id:
                job['cancel'].set();self._jobs.pop(key,None)
        self.cancel_prefetch(plan_id)

    def cancel_prefetch(self, plan_id=None, version=None):
        with self._prefetch_lock:
            for pid,group in list(self._prefetches.items()):
                if (plan_id is None or pid==plan_id) and (version is None or version==group['version']):
                    for job in group['jobs'].values():job['cancel'].set()
                    self._prefetches.pop(pid,None)

    def _teaching_version(self, course, state):
        if state.get('pending') or state.get('direct_review') or state['step'] not in ('teach','opening','teaching','closing'):return None
        try:excerpts=self._teaching_excerpts(course,state,self._focus(course,state))
        except (AgentFormatError,BridgeError):return None
        value=dict(state=state,points=course.points,modules=course.modules,dialogue=self._dialogue(course,state),
            prefs=self._prefs(course),profile=course.store.records('teacher_profile',course.plan_id),
            excerpts=excerpts)
        return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

    def _predicted_teacher(self, course, state, action, args):
        """Describe the next teacher request without performing any course writes."""
        state=copy.deepcopy(state)
        if action=='choose':
            return self._teacher_context(course,state,EXPLAIN,self._focus(course,state),user=state['conversation']['options'][args[0]])
        if action=='confused':return self._teacher_context(course,state,CONFUSED,self._focus(course,state),user='没太懂')
        concept=self._concept(course,state)
        recap=None
        if concept and state['step'] in ('teaching','closing'):
            point,sub,index=concept
            if index+1<len(point['subpoints']):
                state['concept'].update(subpoint_id=point['subpoints'][index+1]['id'],rounds=0)
                return self._teacher_context(course,state,CONCEPT_START,[point['id']])
            points=course.points_of(state['module_id'])
            if state['index']+1>=len(points) or self._target_met_after_point(course,state,point):
                return None
            recap=self._recap_content(point)
            state.update(index=state['index']+1,step='teach',pending=None)
            state.pop('conversation',None);state.pop('concept',None)
        elif state['step'] in ('teaching','closing'):
            state.update(index=state['index']+1,step='teach',pending=None)
            state.pop('conversation',None);state.pop('concept',None)
        points=course.points_of(state['module_id'])
        if state['index']>=len(points):return None
        point=points[state['index']]
        if not course.passages([point['id']]):return None
        if point.get('structure')=='concept_v1' and point.get('subpoints'):
            state['concept']=dict(point_id=point['id'],subpoint_id=point['subpoints'][0]['id'],rounds=0,
                style_history=state.get('concept_recent_styles',[]),intro_endings=[])
            context=self._teacher_context(course,state,CONCEPT_START,[point['id']])
        else:
            context=self._teacher_context(course,state,self._opening_director(course,state),[point['id']],opening=True)
        if recap is not None:context['dialogue']=self._dialogue(course,state,recap=recap)
        return context

    def _target_met_after_point(self, course, state, point):
        """Project this credit with the existing FIFO ledger, without any writes."""
        from datetime import date
        try:
            today=self._today(course,state)
            ledger=course.store.load_ledger(course.plan_id)
            minutes=(self._learning_remainder(course,point)
                if point.get('structure')=='concept_v1' and point.get('subpoints') else point['minutes'])
            if minutes:
                ledger.add_tentative(f"learn:{point['id']}",point['id'],minutes,date.fromisoformat(self._day(state)))
            status=ledger.status(date.fromisoformat(today['progress_day']))
            return status.today_target>0 and status.today_confirmed+status.today_tentative>=status.today_target
        except Exception:
            # An uncertain prediction never speculates beyond a completion barrier.
            return True

    def prefetch(self, plan_id, version):
        lock=self._lock(plan_id)
        if not lock.acquire(False):return dict(total=0,ready=0)
        try:
            course=self._course(plan_id);state=self._load(course)
            if not version or version!=self._teaching_version(course,state):
                self.cancel_prefetch(plan_id,version)
                return dict(total=0,ready=0)
            with self._prefetch_lock:
                existing=self._prefetches.get(plan_id)
                if existing and existing['version']==version:
                    return dict(total=len(existing['jobs']),ready=sum(j['done'] for j in existing['jobs'].values()))
                self.cancel_prefetch()
                actions=([('choose',(i,)) for i in range(len((state.get('conversation') or {}).get('options',[])))]
                         if state['step']=='opening' else [('next',()),('confused',())] if state['step'] in ('teaching','closing') else [('next',())])
                group=dict(version=version,jobs={});self._prefetches[plan_id]=group
                for action,args in actions[:4]:
                    tctx=self._predicted_teacher(course,state,action,args)
                    if not tctx:continue
                    tctx['request_page']=copy.deepcopy(tctx.get('request_page',self._teacher_request_page))
                    request=teacher_agent.teacher_request(tctx)
                    job=dict(cancel=threading.Event(),condition=threading.Condition(),chunks=[],done=False,error=None,
                             request=request,tctx=tctx)
                    group['jobs'][(action,args)]=job
                    def generate(job=job):
                        def emit(delta):
                            with job['condition']:job['chunks'].append(delta);job['condition'].notify_all()
                        try:job['result']=self._generate_teacher(plan_id,job['tctx'],job['request'],job['cancel'],emit,prefetched=True)
                        except Exception as exc:
                            if isinstance(exc, AgentFormatError) and not job['cancel'].is_set():
                                from ..connections.faults import present_error
                                log.exception('prefetched teacher format retries exhausted')
                                exc=ModelError('AGENT_FORMAT','回复格式不符合要求。')
                                present_error(self.services.gateway,exc,purpose='teacher',plan_id=plan_id)
                            job['error']=exc
                        finally:
                            with job['condition']:job['done']=True;job['condition'].notify_all()
                    threading.Thread(target=generate,name='classroom-prefetch',daemon=True).start()
                return dict(total=len(group['jobs']),ready=sum(j['done'] for j in group['jobs'].values()))
        finally:lock.release()

    def _take_prefetch(self, course, state, action):
        if not action:return None
        with self._prefetch_lock:
            group=self._prefetches.pop(course.plan_id,None)
            if not group:return None
            selected=(group['jobs'].get((action[0],tuple(action[1]))) if action[0] in ('next','choose','confused')
                      and group['version']==self._teaching_version(course,state) else None)
            for job in group['jobs'].values():
                if job is not selected:job['cancel'].set()
            if selected:
                with selected['condition']:
                    selected['failed_before_click']=bool(selected['done'] and selected['error'])
            return selected

    def _activity_key(self, plan_id):
        return (str(self.services.data_root), self.services.account_id, plan_id)

    def _lock(self, plan_id):
        key = self._activity_key(plan_id)
        with self._mutex:
            return self._room_locks.setdefault(key, threading.Lock())

    def _operation_is_current(self):
        key=getattr(self._local,'operation_key',None)
        token=getattr(self._local,'operation_token',None)
        if key is None or token is None:return True
        with self._mutex:
            active=self._active_operations.get(key)
            return bool(active and active['token']==token and active['generation']==self._local.operation_generation
                        and not active['cancel'].is_set())

    def _operation_was_superseded(self):
        key=getattr(self._local,'operation_key',None)
        token=getattr(self._local,'operation_token',None)
        if key is None or token is None:return False
        with self._mutex:
            active=self._active_operations.get(key)
            return not active or active['token']!=token or active['generation']!=self._local.operation_generation

    def _stream_current(self, ctx, delta):
        if ctx and not ctx.cancel.is_set() and self._operation_is_current():ctx.stream(delta)

    def _slow_call(self, course, ctx, call):
        """Run blocking model/material work without holding the per-plan lock."""
        lock=getattr(self._local,'room_lock',None)
        if lock is None or not getattr(self._local,'lock_held',False):return call()
        if ctx and ctx.cancel.is_set():raise ModelCancelled()
        if not self._operation_is_current():raise _SupersededOperation()
        store_lock=getattr(course.store,'_lock',None)
        owned=getattr(store_lock,'_is_owned',None)
        if callable(owned) and owned():
            raise RuntimeError('classroom slow call entered while this thread owns a store transaction')
        # Publish the complete decision point before yielding the lock. A new
        # action can now continue from this position while the old worker exits.
        state=getattr(self._local,'room_state',None)
        if state is not None:
            checkpoint=copy.deepcopy(state)
            self._save(course,checkpoint)
            self._local.slow_checkpoint=checkpoint
        self._local.lock_held=False
        lock.release()
        result=None;error=None
        try:result=call()
        except BaseException as exc:error=exc
        if self._operation_was_superseded():raise _SupersededOperation() from error
        lock.acquire()
        self._local.lock_held=True
        if self._operation_was_superseded():
            self._local.lock_held=False;lock.release()
            raise _SupersededOperation() from error
        if error is not None:raise error
        if ctx and ctx.cancel.is_set():raise ModelCancelled()
        return result

    def perform(self, plan_id, action, ctx, args=(), request_id=None):
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[\w-]{1,100}", request_id)):
            raise BridgeError("PARAM_INVALID", "请求编号无效。")
        self._local.action = (action, list(args), request_id or uuid.uuid4().hex)
        try:
            return self.run(plan_id, lambda co, st, cx: getattr(self, action)(co, st, cx, *args), ctx)
        finally:
            self._local.action = None

    def run(self, plan_id, work, ctx=None, *, readonly=False):
        lock = self._lock(plan_id)
        if not lock.acquire(False):
            message = "课堂正在处理，请稍后再发；已有内容和位置保留。"
            raise BridgeError("CLASS_BUSY", message)
        self._local.room_lock=lock;self._local.lock_held=True
        self._local.room_state=None;self._local.operation_key=None;self._local.operation_token=None
        self._local.operation_generation=None;self._local.slow_checkpoint=None
        key=self._activity_key(plan_id);token=None
        try:
            if readonly:
                # Polling must only observe the durable position. It must not
                # resume preparation, settle an attempt, or consume a prefetch.
                course=self._course(plan_id);state=self._load(course)
                return self.snapshot(course,state)
            with self._mutex:
                generation=self._operation_generations.get(key,0)+1
                self._operation_generations[key]=generation
                previous=self._active_operations.get(key)
                if previous:previous['cancel'].set()
                cancel=ctx.cancel if ctx else threading.Event()
                token=uuid.uuid4().hex
                self._active_operations[key]=dict(token=token,generation=generation,cancel=cancel)
            self._local.operation_key=key;self._local.operation_token=token;self._local.operation_generation=generation
            course = self._course(plan_id)
            state = self._load(course)
            self._local.room_state=state
            action = getattr(self._local, "action", None)
            if action and action[2] in state.get("completed_requests", []):
                return self.snapshot(course, state)
            original = copy.deepcopy(state)
            self._local.point_checkpoint = None
            self._local.prefetched = self._take_prefetch(course,state,action)
            try:
                if state.get('pending') and not self._gradable(state['pending']['question']) and not (action and action[0]=='leave'):
                    self._replace_subjective(course,state,ctx)
                elif self._test_already_closed(course, state):
                    self._finish_test(course, state, ctx)
                elif state.get('direct_review') and course.book().attempt_closed(state['module_id'],state['direct_review']['attempt']):
                    self._finish_direct_choice(course,state,ctx)
                else:
                    work(course, state, ctx)
                    if not action or action[0]!='leave':
                        self._resume_materials(course, state, ctx)
                        if original.get('phase') != 'practice' and state.get('phase') != 'practice' and not (action and action[0]=='start_practice'):
                            self._schedule_next_module_safely(course, state, entering=bool(action and action[0]=='enter'))
                previous_fault=(original.get('failure') or {}).get('model_fault')
                clear=getattr(self.services,'clear_model_fault_scope',None)
                if previous_fault and callable(clear):clear(previous_fault.get('purpose','teacher'),plan_id)
                state.pop("failure", None)
                if action:
                    retried=getattr(self._local,'action',None)
                    state["completed_requests"] = (state.get("completed_requests", []) + [action[2]] + ([retried[2]] if retried else []))[-40:]
            except ModelCancelled:
                if self._operation_was_superseded():
                    raise BridgeError('CANCELLED','课堂已切换到最新操作。') from None
                if ctx: ctx.stream(RESET)
                self._save(course, self._local.point_checkpoint or self._local.slow_checkpoint or original)
                raise BridgeError("CANCELLED", "已停止，当前题目和位置保留。") from None
            except _SupersededOperation:
                raise BridgeError("CANCELLED", "课堂已切换到最新操作。") from None
            except (ModelError, AgentFormatError, StoreError, BridgeError) as exc:
                state = self._local.point_checkpoint or original
                action=getattr(self._local,'action',None) or action
                fault = None
                if isinstance(exc, (ModelError, AgentFormatError)):
                    from ..connections.faults import present_error
                    if isinstance(exc, AgentFormatError):
                        log.exception('teacher format retries exhausted')
                        exc = ModelError('AGENT_FORMAT', '回复格式不符合要求。')
                    fault = present_error(self.services.gateway, exc, purpose='classroom:'+str(action[0] if action else 'next'), plan_id=plan_id)
                if self._local.point_checkpoint and action:action=('next',[],action[2])
                state["failure"] = dict(message=getattr(exc, "message", str(exc)),
                    model_fault=fault,
                    action=action[0] if action else "next", args=action[1] if action else [],
                    request_id=action[2] if action else uuid.uuid4().hex)
                if ctx: ctx.stream(RESET)
            except Exception:
                log.exception("classroom operation failed for %s", plan_id)
                state = self._local.point_checkpoint or original
                if self._local.point_checkpoint and action:action=('next',[],action[2])
                state["failure"] = dict(message="这一步没有完成，请重试；原来的题目和位置保留。",
                    action=action[0] if action else "next", args=action[1] if action else [],
                    request_id=action[2] if action else uuid.uuid4().hex)
                if ctx: ctx.stream(RESET)
            self._save(course, state)
            return self.snapshot(course, state)
        finally:
            selected=getattr(self._local,'prefetched',None)
            if selected:selected['cancel'].set()
            self._local.prefetched=None
            self._local.point_checkpoint=None
            self._local.slow_checkpoint=None
            with self._mutex:
                active=self._active_operations.get(key)
                if token and active and active['token']==token:self._active_operations.pop(key,None)
            self._local.room_state=None;self._local.room_lock=None;self._local.operation_key=None;self._local.operation_token=None
            if getattr(self._local,'lock_held',False):lock.release()
            self._local.lock_held=False

    def _load(self, course):
        try:
            store=course.store
            if not store.classroom_position_imported(course.plan_id):
                legacy=self._file(course.plan_id)
                state=legacy.read({})
                stamp=legacy.path.stat().st_mtime_ns if legacy.path.exists() else 0
                store.import_classroom_position(course.plan_id,self._pack_position(course,state),stamp)
            record=store.classroom_position(course.plan_id)
            state=self._unpack_position(course,record['state']) if record else {}
            if record and record['device_id'] != store.classroom_device_id():
                # Session day, local failures and worker progress belong to the
                # producing process. The question/answer IDs and scene remain.
                state.update(day=None,in_session=False)
                for key in ('failure','materials_paused','materials_attempted','preparation_module_entered'):
                    state.pop(key,None)
                if state.get('material_wait'):
                    state['material_wait'].update(done=0,total=0,text='正在准备题目',error=None)
        except (StorageError,StoreError,OSError) as exc:
            raise BridgeError("CLASS_READ_FAILED", "课堂位置读取失败，请重试；不会覆盖已有位置。") from exc
        modules = {m['id'] for m in course.modules}
        if state.get('version') != 2 or state.get('module_id') not in modules:
            state = self._rebuild(course)
        state=self._reconcile_position(course,state)
        room = (state.get('practice') or {}).get('resume', state)
        bank = course.store.records('question_bank',course.plan_id)
        bank_ids = {q['id'] for q in bank}
        retired = {q['id'] for q in bank if q.get('role')=='retired'}
        pending = room.get('pending') or {}
        # Old unscored casual/quick cards already have a dialogue-only reopen
        # path below; they must not reset the current teaching scene.
        cached = [] if pending.get('kind') in ('casual','quick') else [pending.get('question', {})]
        for key in ('test','check','practice'):
            cached.extend((room.get(key) or {}).get('queue', []))
        if any(isinstance(q,dict) and (q.get('id') in retired or q and 'kind' not in q
               or q and q.get('kind') not in ('objective','recall') and q.get('id') not in bank_ids
               and (q.get('kind') not in ('short','essay') or course.plan.get('material_migration')==8)) for q in cached):
            # The scene can cache full legacy cards independently of SQLite.
            # Rebuild only the pending scene from immutable progress; don't
            # change answers, taught credits or closed attempts to retire it.
            state = self._rebuild(course)
            room = state
        if (room.get('pending') or {}).get('kind') in ('casual','quick'):
            # Reopen an old unscored card as conversation, without grading it
            # or deleting any previously saved learning history.
            room.update(pending=None,step='teaching')
            room.pop('answer_explanation',None);room.pop('failure',None)
        if (room['step'] == 'completed') != (course.plan['state'] == 'completed'):
            state = self._rebuild(course)
        review=state.get('direct_review')
        if review and (any(pid not in {p['id'] for p in course.points_of(state['module_id'])} for pid in review['points'])
                       or self._questions(course,state['module_id'])):
            # A bank/source edit must not leave an obsolete unscored scene open.
            self._restore_direct(state,review['resume'])
        return self._reconcile_kept_check(course,state)

    def _reconcile_kept_check(self, course, state):
        """An explicit keep-one grant may supersede a saved, already-passed card.

        Reconcile only this operator action, not ordinary spacing questions or
        any answer history. An existing keep card retains its reveal/ticks.
        """
        if state.get('phase')!='check' or state.get('step')!='checking':return state
        grants=[row for row in course.store.records('judgements',course.plan_id)
                if (row.get('details') or {}).get('user_override') is True
                and (row.get('details') or {}).get('grant_scope')=='except_question']
        if not grants:return state
        latest=max(grants,key=lambda row:(row['event']['seq'],row['id']))
        details=latest['details'];grant_id=details['grant_id'];keep=details['keep_question_id']
        if state.get('keep_question_grant')==grant_id:return state
        book=course.book()
        unresolved=[q['id'] for q in self._questions(course) if not book.question(q['id']).passed]
        if unresolved!=[keep]:return state
        state['keep_question_grant']=grant_id
        if (state.get('pending') or {}).get('question',{}).get('id')==keep:return state
        state.update(pending=None,check=None,check_used=0,resume_kept_check=True)
        state.pop('failure',None);state.pop('recall_result',None)
        return state

    def _reconcile_position(self, course, state):
        if state.get('phase','learning') == 'learning' and state.get('step') in ('teach','opening','teaching','closing','test_offer'):
            points=course.points_of(state['module_id'])
            index=state.get('index',0)
            if (course.book().module(state['module_id']).state in ('passed','skipped')
                    or state['step'] in ('teach','opening') and 0 <= index < len(points) and points[index]['id'] in course.taught()):
                # Only stale teaching starts, never a quiz or its result page.
                # Immutable answers/credits are untouched by this projection.
                return self._rebuild(course)
        return state

    def _save(self, course, state):
        try:
            course.store.save_classroom_position(course.plan_id,self._pack_position(course,state))
        except (StorageError,StoreError) as exc:
            raise BridgeError('CLASS_SAVE_FAILED', '课堂位置保存失败，请重试；已入库判定会按编号恢复。') from exc

    @staticmethod
    def _pack_position(course, state):
        """Queues reference synced bank rows, not hundreds of repeated source bodies.

        The pending card stays a full snapshot (including reveal/ticks), so an
        in-flight question is exactly the one already displayed on the device.
        """
        bank={q['id'] for q in course.store.records('question_bank',course.plan_id)}
        def pack(value):
            if isinstance(value,list):return [pack(v) for v in value]
            if not isinstance(value,dict):return value
            return {k:([{'bank_id':q['id']} if isinstance(q,dict) and q.get('id') in bank else pack(q) for q in v]
                       if k=='queue' and isinstance(v,list) else pack(v)) for k,v in value.items()}
        return pack(state)

    @staticmethod
    def _unpack_position(course, state):
        bank={q['id']:q for q in course.store.records('question_bank',course.plan_id)}
        def unpack(value):
            if isinstance(value,list):return [unpack(v) for v in value]
            if not isinstance(value,dict):return value
            if set(value)=={'bank_id'}:return copy.deepcopy(bank.get(value['bank_id'],dict(id=value['bank_id'])))
            return {k:unpack(v) for k,v in value.items()}
        return unpack(state)

    def _rebuild(self, course):
        book, taught = course.book(), course.taught()
        module = next((m for m in course.modules if book.module(m['id']).state not in ('passed','skipped')),course.modules[-1])
        points = course.points_of(module['id'])
        index = next((i for i,p in enumerate(points) if p['id'] not in taught),len(points))
        if course.plan['state'] in ('checking','completed'):
            step = 'completed' if course.plan['state']=='completed' else 'checking'
        elif all(book.module(m['id']).state in ('passed','skipped') for m in course.modules):
            step = 'check_offer'
        else:
            step = 'teach' if index < len(points) else ('test_offer' if book.module(module['id']).state=='untested' else 'test_done')
        return dict(version=2,module_id=module['id'],index=index,step=step,pending=None,test=None,check=None,
                    phase='check' if step in ('checking','completed') else 'learning',day=None,in_session=False)

    def snapshot(self, course, state):
        book, taught = course.book(), course.taught()
        module = course.module(state['module_id']);points = course.points_of(module['id'])
        pending = state.get('pending');q = pending['question'] if pending else None
        messages = []
        passages=course.passages([p['id'] for p in course.points])
        cards_by_id={}
        for m in course.store.messages(course.plan_id,self._channel(state)):
            cards=[]
            for cid in dict.fromkeys(re.findall(r'mf-source:([^\s)]+)',m['content'])):
                if cid in passages:
                    if cid not in cards_by_id:cards_by_id[cid]=course.passage_card(cid)
                    cards.append(cards_by_id[cid])
            message=dict(id=m['id'],role=m['role'],kind=m.get('kind','text'),content=m['content'],sources=cards)
            if m.get('kind')=='recap':
                try:
                    recap=json.loads(m['content'])
                    if isinstance(recap,dict) and isinstance(recap.get('title'),str) and isinstance(recap.get('items'),list):message['recap']=recap
                except (ValueError,TypeError):pass
            messages.append(message)
        question = {k:q.get(k, []) if k=='options' else q.get(k) for k in ('id','kind','stem','options','source')} if q else None
        if question and q['kind']=='objective':
            graded=question_bank.grading_question(q);question['question_type']=graded['kind']
            if graded['kind']=='fill':question['blanks']=len(graded['answer'])
        if question and q['kind']=='recall':
            question['revealed']=bool(pending.get('revealed'))
            if isinstance(question.get('source'),dict):
                question['source']={k:v for k,v in question['source'].items() if k!='text'}
            if pending.get('revealed'):question.update(reference=q['reference'],key_points=q['key_points'],
                key_point_headings=question_bank.recall_key_point_headings(q),
                answer=pending['learner_answer'],ticked=pending.get('ticked',[]))
        if question:question.update(purpose=PURPOSE[pending['kind']],number=pending.get('number'),total=pending.get('total'))
        today = self._today(course,state)
        concept=self._concept(course,state)
        concept_view=None
        if concept:
            point,sub,index=concept
            concept_view=dict(point_id=point['id'],title=point['title'],goal=point.get('goal',point['summary']),
                subpoint_id=sub['id'],title_current=sub['title'],number=index+1,total=len(point['subpoints']),
                rounds=(state.get('concept') or {}).get('rounds',0),notes=[dict(text=n['text'],source=course.passage_card(n['chunk_id'])) for n in sub.get('notes',[])])
        navigation=[dict(id=n['id'],title=n['title'],reason=n.get('reason',''),
            sources=[course.passage_card(cid) for cid in n.get('chunk_ids',[])]) for n in module.get('navigation',[])]
        # A slow teacher call can release the room lock while the shared
        # preparation worker updates module readiness. Keep teaching delivery
        # on its request snapshot, but project readiness from a fresh store read.
        readiness_course=self._course(course.plan_id)
        all_questions=self._questions(readiness_course)
        questions_by_module={m['id']:[] for m in readiness_course.modules}
        for item in all_questions:questions_by_module.setdefault(item['module_id'],[]).append(item)
        resumes=state.get('module_resumes',{})
        notice=state.get('material_notice')
        check_gate=state.get('step') in ('check_offer','checking') or state.get('phase')=='check'
        readiness_targets={module['id'],*(entry.get('module_id') for entry in resumes.values() if isinstance(entry,dict))}
        if notice:readiness_targets.add(notice.get('module_id'))
        if check_gate:readiness_targets.update(m['id'] for m in readiness_course.modules)
        ready_by_module={mid:self._ready(readiness_course,mid,questions=questions_by_module.get(mid,[]))
            for mid in readiness_targets if mid in questions_by_module}
        if notice and ready_by_module.get(notice.get('module_id'),False):notice=None
        notice_module=(notice or {}).get('module_id',module['id'])
        continuation=self._module_continuation(book,module['id'],
            questions_by_module.get(module['id'],[]),ready_by_module.get(module['id'],False))
        retestable=continuation=='retest'
        preparing = self._jobs.get((course.plan_id, notice_module)) or self._jobs.get((course.plan_id,module['id']))
        current_preparation = ({k:preparing[k] for k in ('module_id','done','total','text','error')}
            if preparing and preparing['status'] in ('running','queued','failed') and not preparing['cancel'].is_set()
            and state.get('phase')=='learning' else None)
        deferred=[];module_options=[]
        for candidate in course.modules:
            mid=candidate['id'];module_state=book.module(mid).state
            if module_state in ('passed','skipped'):continue
            saved=resumes.get(mid)
            if mid!=module['id']:
                if saved and (saved.get('step')=='test_offer'
                              or saved.get('step')=='test_done' and module_state=='failed'):
                    saved_step=saved.get('step','teach')
                    kind='module_test' if saved_step=='test_offer' else 'retest' if saved_step=='test_done' and module_state=='failed' else 'skip_test'
                    row=dict(module_id=mid,title=candidate['title'],ready=ready_by_module.get(mid),kind=kind,step=saved_step)
                    deferred.append(row)
            module_options.append(dict(module_id=mid,title=candidate['title'],current=mid==module['id'],
                ready=ready_by_module.get(mid),state=module_state,step=state['step'] if mid==module['id'] else (saved or {}).get('step','teach')))
        return dict(plan={k:course.plan.get(k) for k in ('id','goal','state')},
            module=dict(id=module['id'],title=module['title'],number=course.modules.index(module)+1,total=len(course.modules),state=book.module(module['id']).state,navigation=navigation),
            points=[dict(id=p['id'],title=p['title'],minutes=p['minutes'],taught=p['id'] in taught,state=book.point(p['id']).state,current=i==state['index']) for i,p in enumerate(points)],
            step=state['step'],question=question,messages=messages,conversation=state.get('conversation'),concept=concept_view,
            teaching_version=self._teaching_version(readiness_course,state),
            preparation=state.get('material_wait') or current_preparation,material_notice=notice,
            preparation_error=state.get('preparation_error'),
            retestable=retestable,
            module_continuation=continuation,
            test_readiness=dict(module_test=ready_by_module.get(module['id'],False),
                skip_test=ready_by_module.get(module['id'],False),retest=retestable,
                # The final-check control remains actionable; start_check
                # routes a stale offer back to the module that still needs work.
                check=check_gate),
            deferred_module_tests=deferred,module_options=module_options,
            failure=state.get('failure'),explanation=state.get('answer_explanation'),
            module_assessment=(self._module_assessment_status(book,module['id'])
                               if state.get('step')=='test_done' else None),
            can_skip=self._can_direct(course,state),
            direct_review=self._direct_view(course,state),
            red=[p['title'] for p in points if book.point(p['id']).state=='red'],
            more_modules=any(book.module(m['id']).state=='untested' for m in course.modules if m['id']!=module['id']),
            recall_result=state.get('recall_result'),
            check=dict(passed=sum(status is not None and status.passed
                for status in (self._question_state(book,q['id']) for q in all_questions)),
                total=len(all_questions)) if state.get('phase')=='check' or state['step'] in ('check_offer','completed') else None,today=today)

    @staticmethod
    def _materials():
        try:return importlib.import_module('refactor.learning.course.point_materials')
        except ImportError as exc:raise BridgeError('MATERIAL_COMPONENT_MISSING','资料与题库组件尚未接入，请稍后重试。') from exc

    def _ready(self, course, module_id, *, questions=None):
        module=course.module(module_id);status=module.get('material_preparation',{}).get('state')
        passages=all(course.passages([p['id']]) for p in course.points_of(module_id))
        questions = self._questions(course,module_id) if questions is None else questions
        if module.get('question_count') is not None and module['question_count']!=len(questions):
            return False
        return passages and (status=='done' or status is None and bool(questions))

    def _fingerprint(self, course, module_id):
        data=[{k:v for k,v in p.items() if k!='chunk_ids'} for p in course.points_of(module_id)]
        return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    def _preparation_flow(self):
        flow = getattr(self.services, '_material_flow', None)
        if flow is None:
            from .plan_flow import PlanFlow
            flow = PlanFlow(self.services, lambda *_: None, self.study_day)
        return flow

    def _prepare(self, course, module_id, *, retry=False):
        key=(course.plan_id,module_id);fingerprint=self._fingerprint(course,module_id)
        job=self._preparation_flow()._prepare_remaining(course.plan_id,
            [dict(course.module(module_id), points=course.points_of(module_id))], self._materials().prepare_module,
            retry=retry, fingerprint=fingerprint, notify=self.material_event, priority=True)
        if job is None:raise BridgeError('ACCOUNT_CLOSED','账号已关闭，请重新进入。')
        self._jobs[key]=job
        return job

    def _prepare_next_module(self, course, state, *, entering=False):
        # Resume the whole persisted tail; current classroom demand only changes
        # its position in the existing queue, never cancels another module.
        if state.get('materials_paused') or course.plan.get('state')=='completed':
            return
        mid=state['module_id']
        entered=entering or state.get('preparation_module_entered')!=mid
        state['preparation_module_entered']=mid
        # One bank read per readiness pass; no persistent cache across actions.
        by_module={m['id']:[] for m in course.modules}
        for question in self._questions(course):
            by_module.setdefault(question['module_id'],[]).append(question)
        remaining=[m for m in course.modules if not self._ready(course,m['id'],questions=by_module[m['id']])]
        if not remaining:return
        if any(m['id']==mid for m in remaining) and state.get('materials_attempted',{}).get(mid)!=self._fingerprint(course,mid):
            self._prepare(course,mid,retry=entered)
        self._preparation_flow()._prepare_remaining(course.plan_id,
            [dict(m, points=course.points_of(m['id'])) for m in remaining], self._materials().prepare_module)

    def _schedule_next_module_safely(self, course, state, *, entering=False):
        """Queue optional background materials without rolling back lesson work."""
        try:
            # Preparation state may have advanced while this action was in a
            # slow teacher call. Scheduling uses current persisted module data;
            # teacher/source delivery keeps the caller's original Course.
            self._prepare_next_module(self._course(course.plan_id),state,entering=entering)
            state.pop('preparation_error',None)
        except Exception:  # noqa: BLE001 - only this optional queue submission is isolated
            log.exception('optional classroom material scheduling failed for %s/%s',
                          course.plan_id,state.get('module_id'))
            state['preparation_error']=dict(module_id=state.get('module_id'),
                message='题目准备没有启动，课堂可以继续；需要检测时可重试。')

    def _wait(self, course, state, action, args=()):
        """Legacy resumable foreground wait; teaching no longer enters this path."""
        if action not in ('start_test','start_check'):
            raise AssertionError('foreground material waits are reserved for legacy assessment actions')
        mid=state['module_id'];job=self._prepare(course,mid)
        state['material_wait']=dict(module_id=mid,action=action,args=list(args),previous_step=state['step'],done=job['done'],total=job['total'],text=job['text'],error=job['error'])
        state['step']='preparing_materials'

    @staticmethod
    def _normalize_unknown_material_wait(state):
        wait=state.get('material_wait')
        if not wait or wait.get('action') in ('start_test','start_check'):
            return False
        previous=wait.get('previous_step')
        valid=('teach','opening','teaching','closing','test_offer','test_done','check_offer','checking',
               'direct_review','direct_choice','practice','completed')
        state['step']=previous if previous in valid else 'teach'
        state.pop('material_wait',None)
        log.warning('discarded unsupported legacy material wait action %r',wait.get('action'))
        return True

    def _resume_materials(self, course, state, ctx):
        wait=state.get('material_wait')
        if not wait:return
        if self._normalize_unknown_material_wait(state):return
        job=self._jobs.get((course.plan_id,wait['module_id'])) or self._prepare(course,wait['module_id'])
        wait.update(done=job['done'],total=job['total'],text=job['text'],error=job['error'])
        # Preserve the existing per-point shortage fallback after a completed
        # attempt. This task does not change the module readiness rules.
        if job['status']=='ready' or (job['status']=='failed' and job.get('code')=='MATERIALS_INCOMPLETE'):
            if self._fingerprint(self._course(course.plan_id),wait['module_id'])!=job['fingerprint']:
                self._prepare(course,wait['module_id'],retry=True)
                return
            state.setdefault('materials_attempted',{})[wait['module_id']]=job['fingerprint']
            state['step']=wait['previous_step'];state.pop('material_wait')
            getattr(self,wait['action'])(course,state,ctx,*wait['args'])

    def enter(self, course, state, ctx):
        self._normalize_unknown_material_wait(state)
        if state.pop('resume_kept_check',False):self._next_check(course,state,ctx)
        if (course.book().check_complete() and not self._questions(course) and course.plan['state']!='completed'
                and all(self._ready(course,m['id']) for m in course.modules)):
            return self._complete(course,state,ctx)
        state.pop('materials_paused',None)
        if course.plan_id not in self._entered_plans or not state.get('in_session'):
            state['day']=self.study_day().isoformat()
        self._entered_plans.add(course.plan_id);state['in_session']=True

    def leave(self, course, state, ctx):
        self._entered_plans.discard(course.plan_id)
        # Leaving a lesson cancels speculative replies, not the shared module
        # preparation queue. The next visit must reuse its in-flight work.
        self.cancel_prefetch(course.plan_id)
        state['in_session']=False;state['day']=None
        self._profile(course)

    def retry(self, course, state, ctx):
        state.pop('materials_paused',None)
        preparation_error=state.pop('preparation_error',None)
        failure=state.pop('failure',None)
        if failure:
            self._local.action=(failure['action'],failure['args'],failure['request_id'])
            return getattr(self,failure['action'])(course,state,ctx,*failure['args'])
        wait=state.get('material_wait')
        if wait:
            job=self._jobs.pop((course.plan_id,wait['module_id']),None)
            if job:job['cancel'].set()
            self._prepare(course,wait['module_id'],retry=True)
        elif state.get('material_notice'):
            mid=state['material_notice']['module_id']
            job=self._jobs.get((course.plan_id,mid))
            if not job or job['status'] in ('failed','cancelled','ready'):
                self._prepare(course,mid,retry=True)
        elif preparation_error:
            self._prepare(course,preparation_error['module_id'],retry=True)
        elif (self._jobs.get((course.plan_id,state['module_id'])) or {}).get('status')=='failed':
            self._prepare(course,state['module_id'],retry=True)

    def stop_preparation(self, course, state, ctx):
        # Leave only the foreground wait. The shared preparation queue keeps
        # working; returning to learning must not cancel or pause its jobs.
        state.pop('materials_paused',None)
        wait=state.pop('material_wait',None)
        if wait:state['step']=wait['previous_step']
        state.pop('material_notice',None)

    def next(self, course, state, ctx):
        state.pop('materials_paused',None)
        if state['step']=='preparing_materials':return
        concept=self._concept(course,state)
        if concept and state['step'] in ('teaching','closing'):
            point,sub,index=concept
            # A subpoint becomes learning progress as soon as the learner
            # advances away from it.  Keep its event ID independent from the
            # whole-point marker so an interrupted lecture is not mistaken
            # for a completed point by _Course.taught().
            self._credit_subpoint(course,state,point,sub)
            self._publish_today(course,state,ctx)
            if index+1<len(point['subpoints']):
                return self._concept_begin(course,state,ctx,index+1)
            self._recap(course,state,point)
            state.pop('concept',None)
            self._point_done(course,state)
            self._publish_today(course,state,ctx)
            if state['step']!='teach' or state['module_id']!=point['module_id'] or self._target_met(course,state):return
            # The recap and credit are committed facts. A later teacher failure
            # must retry only the new point, including a typed “懂了” action.
            self._save(course,state)
            self._local.point_checkpoint=copy.deepcopy(state)
        elif state['step'] in ('teaching','closing'):
            self._point_done(course,state)
            self._publish_today(course,state,ctx)
            if state['step']!='teach':return
            if self._target_met(course,state):return
        if state['step']!='teach':raise BridgeError('STEP_INVALID','请先完成当前的对话或题目。')
        point=course.points_of(state['module_id'])[state['index']]
        if not course.passages([point['id']]):
            # Lesson progression may use only already available source material.
            # Never start slow question/material generation just to enter a lesson.
            state.setdefault('question_gaps',[]).append(dict(point_id=point['id'],title=point['title'],
                purpose='learning',reason='没有关联的原文或讲义，未计入学习进度',kind='material_gap',
                code='POINT_SOURCE_MISSING',day=self._day(state)))
            self._say(course,state,f"{point['title']}：目前没有可用的原文或讲义，暂时无法讲解；未计入学习进度。",role='system')
            state['index']+=1
            state['step']='teach' if state['index']<len(course.points_of(state['module_id'])) else 'test_offer'
            return
        if point.get('structure')=='concept_v1' and point.get('subpoints'):
            return self._concept_begin(course,state,ctx,0)
        teaching=self._teacher(course,state,ctx,self._opening_director(course,state),[point['id']],opening=True)
        self._deliver(course,state,teaching)
        options=list(teaching.options)
        if '直接开始讲' not in options:options.append('直接开始讲')
        state.update(step='opening',conversation=dict(point_id=point['id'],options=options,interrupted=False))

    @staticmethod
    def _opening_director(course,state):
        chapter=f"先用一句话说明这一章要解决什么问题：{course.module(state['module_id'])['title']}。" if state['index']==0 else ''
        return chapter+'用一个生活化问题开头，给2～3个不计分的可点选项，也允许打字。先不要讲完整结论。'

    @staticmethod
    def _recap_content(point):
        items=[]
        for sub in point['subpoints']:
            candidates=[];notes=[]
            for note in sub.get('notes',[]):
                text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',note['text']).replace('**','').replace('`','')
                text=re.sub(r'^\s*(?:[-*#]+\s+)?', '',text).strip()
                notes.append((text,note['chunk_id']))
                if re.match(r'^\s*(?:#{1,6}\s+|\*\*\d+[.、])',note['text']):continue
                sentences=re.findall(r'[^。！？!?\n]+[。！？!?]',text)
                candidates.extend((s.strip(),note['chunk_id']) for s in sentences if s.strip())
            # Prefer a concise complete source sentence, preserving note order.
            short=next((row for row in candidates if len(row[0])<=80),None)
            chosen=short or (candidates[0] if candidates else None)
            if not chosen:
                chosen=notes[0] if notes else ('（该小要点暂无讲义）',None)
            items.append(dict(title=sub['title'],text=chosen[0],chunk_id=chosen[1]))
        return dict(title=point['title'],items=items)

    def _recap(self, course, state, point):
        self._say(course,state,json.dumps(self._recap_content(point),ensure_ascii=False),role='assistant',kind='recap')

    @staticmethod
    def _recap_text(recap):
        return '要点卡：'+recap['title']+'\n'+'\n'.join(i['title']+'：'+i['text'] for i in recap['items'])

    def skip_point(self, course, state, ctx):
        if state.get('pending') or state.get('direct_review') or state['step'] not in ('teach','opening','teaching','closing'):
            raise BridgeError('STEP_INVALID','请先完成当前答题。')
        self._point_done(course,state)
        self._publish_today(course,state,ctx)
        if state['step']=='teach':return self.next(course,state,ctx)

    @staticmethod
    def _concept(course,state):
        if state.get('pending') or state.get('direct_review') or state.get('phase','learning')!='learning' or state.get('step') not in ('teach','opening','teaching','closing'):
            return None
        points=course.points_of(state['module_id'])
        if not 0<=state['index']<len(points):return None
        point=points[state['index']]
        if point.get('structure')!='concept_v1' or not point.get('subpoints'):return None
        saved=state.get('concept') or {}
        index=next((i for i,s in enumerate(point['subpoints']) if saved.get('point_id')==point['id'] and s['id']==saved.get('subpoint_id')),0)
        return point,point['subpoints'][index],index

    def _concept_begin(self,course,state,ctx,index):
        point=course.points_of(state['module_id'])[state['index']]
        sub=point['subpoints'][index]
        prior=state.get('concept') or {}
        state['concept']=dict(point_id=point['id'],subpoint_id=sub['id'],rounds=0,
            style_history=prior.get('style_history',[]) if prior.get('point_id')==point['id'] else state.get('concept_recent_styles',[]),
            intro_endings=prior.get('intro_endings',[]) if prior.get('point_id')==point['id'] else [])
        teaching=self._teacher(course,state,ctx,CONCEPT_START,[point['id']])
        self._deliver(course,state,teaching,explained=True)
        state.update(step='teaching',conversation=dict(point_id=point['id'],options=[],interrupted=False))

    def _teaching_excerpts(self,course,state,point_ids):
        concept=self._concept_context(course,state,point_ids)
        if concept:
            if concept[3] and (state.get('concept') or {}).get('wrapping'):
                _,chunk_ids,*_=self._concept_wrap_material(concept[0],concept[2])
                # Keep real chunk IDs for citation resolution, but never attach
                # source paragraphs to a large-point closing request.
                return {cid:'讲义出处' for cid in chunk_ids}
            chunks,sources=course._source_rows()
            wanted=concept[1].get('chunk_ids') or []
            return {cid:chunks[cid]['text'][:40000] for cid in wanted
                    if cid in chunks and chunks[cid].get('text','').strip()
                    and sources.get(chunks[cid].get('source_id'),{}).get('adopted')}
        return course.excerpts(point_ids)

    def _concept_context(self,course,state,point_ids):
        """Resolve the relevant small point even when a quiz suspends teaching."""
        active=self._concept(course,state)
        if active and point_ids==[active[0]['id']]:return (*active,True)
        if len(point_ids)!=1:return None
        try:point=course.point(point_ids[0])
        except StopIteration:return None
        subpoints=point.get('subpoints') or []
        if point.get('structure')!='concept_v1' or not subpoints:return None

        # A pending bank question carries the most precise scope.
        question=(state.get('pending') or {}).get('question') or {}
        if state.get('pending'):
            sid=question.get('subpoint_id')
            index=next((i for i,s in enumerate(subpoints) if s['id']==sid),None)
            if index is not None:return point,subpoints[index],index,False
            source=question.get('source') or {}
            chunk_ids=set(question.get('chunk_ids') or ())
            if isinstance(source,dict):
                chunk_ids.update(source.get('chunk_ids') or ())
                if source.get('chunk_id'):chunk_ids.add(source['chunk_id'])
            if chunk_ids:
                index=next((i for i,s in enumerate(subpoints) if chunk_ids.intersection(s.get('chunk_ids') or ())),None)
                if index is not None:return point,subpoints[index],index,False

        # Persisted position wins for direct_review; its resume snapshot is the
        # exact subpoint the learner was on before opening the review.
        review=state.get('direct_review') or {}
        practice=state.get('practice') or {}
        resume=review.get('resume') or practice.get('resume') or {}
        saved=resume.get('concept') or state.get('concept') or {}
        if saved.get('point_id')==point['id']:
            index=next((i for i,s in enumerate(subpoints) if s['id']==saved.get('subpoint_id')),None)
            if index is not None:return point,subpoints[index],index,False
        # A direct-review point without a saved concept position is deliberately
        # scoped to one small point; never fall back to its entire big point.
        return point,subpoints[0],0,False

    @staticmethod
    def _concept_wrap_material(point,current_index=None):
        """Return complete, source-linked note sentences within a hard budget."""
        subpoints=point.get('subpoints') or []
        def complete_prefix(text,limit):
            text=str(text or '').strip()
            kept=[]
            for sentence in re.findall(r'[^。！？!?]+[。！？!?]?|[。！？!?]',text):
                sentence=sentence.strip()
                if not sentence:continue
                candidate=''.join(kept)+sentence
                if len(candidate)>limit:break
                kept.append(sentence)
            return ''.join(kept)
        goal=complete_prefix(point.get('goal') or point.get('summary',''),1200)
        point_title=str(point.get('title') or '')
        if len(point_title)>500:point_title='（大知识点标题过长，已省略）'
        if current_index is None:current_index=len(subpoints)-1
        current_index=max(0,min(current_index,len(subpoints)-1)) if subpoints else 0
        # Titles are whole atoms, never clipped. Reserve half the budget for
        # notes and provenance; include the current title first, then earlier
        # titles in order, and report how many could not fit.
        title_budget=CONCEPT_WRAP_CONTEXT_CHARS//4
        selected_titles=[];title_cost=0
        order=([current_index]+[i for i in range(len(subpoints)) if i!=current_index])
        for i in order:
            title=str(subpoints[i].get('title') or '')
            cost=len(json.dumps(title,ensure_ascii=False))+2
            if title and title_cost+cost<=title_budget:
                selected_titles.append((i,title));title_cost+=cost
        selected_titles.sort()
        included_indexes={i for i,_ in selected_titles}
        display_titles=[title for _,title in selected_titles]
        completed_titles=[title for i,title in selected_titles if i<current_index]
        current_title=next((title for i,title in selected_titles if i==current_index),
                           '（当前小要点标题过长，已省略）')
        omitted_titles=max(0,len(subpoints)-len(selected_titles))
        omitted_notes=0
        notes=[]
        def bundle_size(candidate):
            ids=list(dict.fromkeys(n['chunk_id'] for n in candidate if n.get('chunk_id')))
            aliases={cid:f'c{i}' for i,cid in enumerate(sorted(ids),1)}
            wire_notes=[dict(n,alias=aliases.get(n['chunk_id'])) for n in candidate]
            payload=dict(title=point_title,goal=goal,completed_titles=completed_titles,
                         title_current=current_title,subpoint_titles=display_titles,
                         omitted_subpoint_titles=omitted_titles,omitted_note_sentences=omitted_notes,
                         subpoint_id=subpoints[current_index]['id'] if subpoints else '',
                         number=current_index+1,total=len(subpoints),wrapping=True,
                         first_turn=False,recent_styles=[],previous_intro_ending=None,
                         notes=wire_notes,source_premises=[],
                         excerpts={cid:'讲义出处' for cid in ids})
            return len(json.dumps(payload,ensure_ascii=False,separators=(',',':')))
        # Metadata repeats titles in the contract fields, so enforce the cap
        # against that actual wire shape, not just the title list itself.
        while selected_titles and bundle_size([])>CONCEPT_WRAP_CONTEXT_CHARS:
            removable=next((j for j in range(len(selected_titles)-1,-1,-1)
                            if selected_titles[j][0]!=current_index),None)
            if removable is None:break
            selected_titles.pop(removable)
            selected_titles.sort()
            included_indexes={i for i,_ in selected_titles}
            display_titles=[title for _,title in selected_titles]
            completed_titles=[title for i,title in selected_titles if i<current_index]
            current_title=next((title for i,title in selected_titles if i==current_index),
                               '（当前小要点标题过长，已省略）')
            omitted_titles=max(0,len(subpoints)-len(selected_titles))
        for i,sub in enumerate(subpoints):
            if i not in included_indexes:continue
            title=str(sub.get('title') or '')
            for note in sub.get('notes') or []:
                cid=note.get('chunk_id')
                text=str(note.get('text') or '').strip()
                if not cid or not text:continue
                # Split only at sentence-ending punctuation. An unpunctuated
                # note is kept whole; no branch clips the tail of a sentence.
                for sentence in re.findall(r'[^。！？!?]+[。！？!?]?|[。！？!?]',text):
                    sentence=sentence.strip()
                    if not sentence:continue
                    row=dict(subpoint_title=title,text=sentence,chunk_id=cid)
                    candidate=[*notes,row]
                    if bundle_size(candidate)<=CONCEPT_WRAP_CONTEXT_CHARS:
                        notes.append(row)
                    else:omitted_notes+=1
        ids=list(dict.fromkeys(n['chunk_id'] for n in notes))
        if not ids:
            ids=list(dict.fromkeys(cid for sub in subpoints for cid in sub.get('chunk_ids',[])))[:1]
        return notes,ids,goal,point_title,completed_titles,current_title,display_titles,omitted_titles,omitted_notes

    def choose(self, course, state, ctx, option):
        conversation=state.get('conversation') or {}
        options=conversation.get('options') or []
        if state['step']!='opening' or type(option) is not int or not 0<=option<len(options):raise BridgeError('PARAM_INVALID','请选择当前开场问题中的一个选项。')
        self._explain(course,state,ctx,options[option])

    def _explain(self, course, state, ctx, text):
        point=self._focus(course,state)[0]
        teaching=self._teacher(course,state,ctx,EXPLAIN,[point],user=text)
        self._deliver(course,state,teaching,user=text,explained=True)
        state['step']='teaching';state['conversation']['options']=[]

    def confused(self, course, state, ctx):
        if state['step'] not in ('opening','teaching','closing'):raise BridgeError('STEP_INVALID','可以先提问说明哪里不明白。')
        text='没太懂'
        teaching=self._teacher(course,state,ctx,CONFUSED,self._focus(course,state),user=text)
        self._deliver(course,state,teaching,user=text)

    def ask(self, course, state, ctx, text):
        if not state.get('pending') and state['step'] in ('opening','teaching','closing'):
            self._student_message(course,state,ctx,text)
            if state.get('conversation'):state['conversation']['interrupted']=True
            return
        # A pending answer is not an active grading request. The explicit ask
        # action never goes through the grader and never changes the position.
        point_ids=self._focus(course,state)
        director='正常回答学生追问，依据本点和本计划已采用资料；没有依据就直说“资料里没讲到”，可在计划里补充资料。回答完回到原来的位置，不出题、不判分。'
        if state.get('pending'):
            director+='当前屏幕上正在检测，学生这句明确是提问，不是作答。只解答这个疑问，不继续讲课、不讲当前题的答案或必答要点、不提示如何答题。题目与检测仍保留。'
            director+='软件操作事实：客观题在选项或输入框作答、程序判；问答题用上方卡片先写自己的回答，再点看答案，要点默认全部勾上，把没答到的点成叉，由学生自己点〔算我答对〕或〔算我答错〕，与勾选数量无关。你只回答提问，不代替卡片、不改变软件的判法。'
            director+='说“继续”不会跳过检测题，也不会退回讲课；不要建议这样跳题，不编造操作。回答完请学生回到当前卡片作答。'
        teaching=self._teacher(course,state,ctx,director,point_ids,user=text,allow_plan=True)
        self._deliver(course,state,teaching,user=text)
        if state.get('conversation'):state['conversation']['interrupted']=True

    def _student_message(self, course, state, ctx, text):
        # One teacher call handles replies and questions without grading or
        # moving the teaching position. The same sources serve both routes.
        teaching=self._teacher(course,state,ctx,
            '根据刚才对话自行辨别学生这句话是在回答理解性问题，还是新问题或没懂的地方。'
            '是回答：理解一致就简单肯定，偏了对照原文说明；口误或表达不完整不纠缠。'
            '是提问：依据本点和本计划已采用资料解答，没有依据直说资料里没讲到。'
            '一条回复完成，不另作分类；答完仍在当前讲解位置，不打分、不说答对或答错，不出题卡。',
            self._focus(course,state),user=text,allow_plan=True)
        self._deliver(course,state,teaching,user=text,explained=state['step']=='opening')
        if state['step']=='opening':
            state['step']='teaching'
            if state.get('conversation'):state['conversation']['options']=[]

    def back(self, course, state, ctx):
        if state.get('conversation'):state['conversation']['interrupted']=False

    def reply(self, course, state, ctx, text):
        if state['step']=='direct_review':
            review=state['direct_review']
            if not review['answered']:
                # Local, temporary comparison only: never enters messages,
                # scored answers, judgements, profiles or teacher context.
                review.setdefault('responses',{})[str(review['index'])]=dict(
                    point_id=review['points'][review['index']],question=review['question'],answer=text)
                review['answered']=True
                if review['index']+1==len(review['points']):state['step']='direct_choice'
            return
        if state['step']=='opening':return self._student_message(course,state,ctx,text)
        if self._concept(course,state) and text.strip() in ('懂了','我懂了','明白了'):
            return self.next(course,state,ctx)
        pending=state.get('pending')
        if not pending:
            if text.strip() in ('继续','懂了，继续','懂了继续','下一个','我会了','我已经会了') and state['step'] in ('teaching','closing'):
                return self.next(course,state,ctx)
            if state['step'] in ('teaching','closing'):
                return self._student_message(course,state,ctx,text)
            return self.ask(course,state,ctx,text)
        q=pending['question']
        prior=self._prior_verdict(course,state,pending)
        if prior:return self._settle(course,state,ctx,pending,prior,text)
        if q['kind']=='recall':return self.recall_reveal(course,state,ctx,q['id'],text)
        q=question_bank.grading_question(q)
        if not answer_compare.can_grade(q):return self._replace_subjective(course,state,ctx)
        if q['kind'] in ('choice','multi'):
            value=answer_compare.choice_text(q,text)
            if value is None:
                self._user(course,state,text)
                return self._say(course,state,'请点题目选项作答；想问老师可以点“我想问…”，当前题目保留。',role='system')
        elif q['kind']=='fill':
            value=[text] if len(q['answer'])==1 else re.split(r'[；;\n]+',text)
        else:value=text
        return self.answer(course,state,ctx,value)

    def answer(self, course, state, ctx, value):
        pending=state.get('pending')
        if not pending:raise BridgeError('STEP_INVALID','现在没有待答题。')
        q=pending['question']
        if q['kind']=='recall':return self.recall_reveal(course,state,ctx,q['id'],value)
        graded=question_bank.grading_question(q)
        try:
            if graded['kind']=='fill' and isinstance(value,str):value=[value]
            good,text=answer_compare.grade(graded,value)
        except ValueError as exc:raise BridgeError('PARAM_INVALID',str(exc)) from None
        verdict=judge_agent.Verdict('answer',good,() if good else tuple(q['point_ids']),
            '作答'+('符合资料标准答案。' if good else '不符合资料标准答案。'))
        pending['learner_answer']=copy.deepcopy(value)
        return self._settle(course,state,ctx,pending,self._prior_verdict(course,state,pending) or verdict,text)

    def recall_reveal(self, course, state, ctx, question_id, answer):
        pending=state.get('pending')
        if not pending or pending['question']['id']!=question_id or pending['question']['kind']!='recall':
            raise BridgeError('STEP_INVALID','请先回答当前的回想题。')
        if not isinstance(answer,str) or not answer.strip() or len(answer)>20000:
            raise BridgeError('PARAM_INVALID','先写下自己的回答，才能查看参考答案。')
        if not pending.get('revealed'):
            # Reopened, not-yet-revealed cards use the current meaning groups.
            # Revealed cards keep their snapshot and already saved tick indices.
            pending['question'] = question_bank.with_recall_key_points(pending['question'])
            pending.update(learner_answer=answer,revealed=True,
                           ticked=list(range(len(pending['question']['key_points']))))
            self._user(course,state,answer)

    def recall_draft(self, course, state, ctx, question_id, ticked):
        pending=state.get('pending')
        if not pending or pending['question']['id']!=question_id or pending['question']['kind']!='recall' or not pending.get('revealed'):
            raise BridgeError('STEP_INVALID','先提交自己的回答、查看参考答案，再勾选要点。')
        count=len(pending['question']['key_points'])
        if not isinstance(ticked,list) or any(type(i) is not int or not 0<=i<count for i in ticked) or len(set(ticked))!=len(ticked):
            raise BridgeError('PARAM_INVALID','勾选的要点下标无效。')
        pending['ticked']=sorted(ticked)

    def recall_submit(self, course, state, ctx, question_id, ticked, correct):
        self.recall_draft(course,state,ctx,question_id,ticked)
        if type(correct) is not bool:raise BridgeError('PARAM_INVALID','请自己选择算对或算错。')
        pending=state['pending'];q=pending['question']
        prior=self._prior_verdict(course,state,pending)
        if prior is not None:
            fact=next((r for r in course.store.records('question_answers',course.plan_id)
                       if r['id']==pending['answer_id']),{})
            ticked=fact.get('ticked',ticked);correct=prior.correct
        missed=[i for i in range(len(q['key_points'])) if i not in ticked]
        pending.update(ticked=sorted(ticked),self_correct=correct)
        verdict=judge_agent.Verdict('answer',correct,() if correct else tuple(q['point_ids']),
            '学生自己选择：'+('算我答对。' if correct else '算我答错。'))
        state['recall_result']=dict(question_id=question_id,correct=correct,missed=missed)
        self._settle(course,state,ctx,pending,prior or verdict,pending['learner_answer'])

    @staticmethod
    def _gradable(q):
        return q.get('kind')=='recall' or answer_compare.can_grade(question_bank.grading_question(q))

    @staticmethod
    def _questions(course, module_id=None):
        return question_bank.assessment_questions(course.store.records('question_bank',course.plan_id),module_id)

    @staticmethod
    def _question_state(book, question_id):
        try:return book.question(question_id)
        except MasteryError as exc:
            if exc.code=='UNKNOWN_QUESTION':return None
            raise

    @classmethod
    def _has_wrong_questions(cls, book, questions):
        return any((status:=cls._question_state(book,q['id'])) is not None
                   and status.module_correct is False for q in questions)

    @classmethod
    def _module_continuation(cls, book, module_id, questions, ready):
        """Single decision for the next useful action on this module."""
        state=book.module(module_id).state
        if state in ('passed','skipped'):return 'next'
        if state=='failed':
            # A stale ready marker cannot make an empty current bank testable;
            # route to preparation instead of presenting an impossible button.
            if not questions:return 'prepare'
            if cls._has_wrong_questions(book,questions):return 'retest'
            return 'module_test' if ready else 'prepare'
        return 'next'

    @classmethod
    def _module_assessment_status(cls, book, module_id):
        module=book.module(module_id)
        manifest=book.stats().get('modules',{}).get(module_id,{})
        question_ids=manifest.get('assessment_ids') or ()
        if not question_ids:return None
        correct=sum((state:=cls._question_state(book,qid)) is not None
                    and state.module_correct is True for qid in question_ids)
        return dict(correct=correct,total=len(question_ids),
                    passed=module.state in ('passed','skipped'))

    def _replace_subjective(self, course, state, ctx):
        pending=state['pending'];old=pending['question']
        self._say(course,state,'旧主观题不计分，换一道可由程序判定的题。',role='system')
        q=self._pick(course,pending['point_ids'][0],PURPOSE[pending['kind']],exclude=(old['id'],),state=state,ctx=ctx)
        state['pending']=None
        if q:return self._show_question(course,state,q,pending['kind'],number=pending.get('number'),total=pending.get('total'))
        return self._advance_missing(course,state,ctx,pending['kind'])


    @staticmethod
    def _reference(q):
        if q['kind']=='recall':return q['reference']
        q=question_bank.grading_question(q)
        if q['kind'] in ('choice','multi'):return '\n'.join(q['options'][i] for i in q['answer'])
        if q['kind']=='fill':return '\n'.join(' / '.join(a) for a in q['answer'])
        return q['answer']

    def _pick(self, course, point_id, purpose, exclude=(), *, state=None, ctx=None, prepare=True):
        from ..course.question_bank import select_question
        q=select_question(course.store.records('question_bank',course.plan_id),point_id,purpose,exclude)
        return copy.deepcopy(q) if q else None


    def _prepare_one(self, course, point_id, ctx, *, exclude_ids=()):
        cancel=ctx.cancel if ctx else threading.Event()
        if cancel.is_set():raise ModelCancelled()
        if ctx:self._stream_current(ctx,MATERIAL_EVENT+json.dumps(dict(text='正在找题…',done=0,total=1),ensure_ascii=False))
        try:
            self._slow_call(course,ctx,lambda:self._materials().prepare_point(course.store,course.plan_id,course.point(point_id),dict(
                gateway=self.services.gateway,cancel=cancel,exclude_ids=exclude_ids,on_status=lambda text:self._stream_current(ctx,MATERIAL_EVENT+json.dumps(dict(text=text,done=0,total=1),ensure_ascii=False)) if ctx else None)))
        except Exception as exc:
            if isinstance(exc,_SupersededOperation):raise
            if cancel.is_set() or isinstance(exc,ModelCancelled):raise ModelCancelled() from None
            log.info('point preparation did not produce a question',exc_info=True)
            if getattr(exc,'code','') in ('CLOZE_FAILED','POINT_SOURCE_MISSING'):
                return dict(kind='question_program_error',code=exc.code,reason=str(exc),
                    recorded=getattr(exc,'evidence_saved',True))
            return dict(kind='material_gap',code=getattr(exc,'code','PREPARATION_UNAVAILABLE'),reason='找题暂未完成，请稍后再试')
        if cancel.is_set():raise ModelCancelled()
        if not self._course(course.plan_id).passages([point_id]):
            log.error('point preparation invariant: POINT_SOURCE_MISSING')
            return dict(kind='question_program_error',code='POINT_SOURCE_MISSING',reason='没有关联的原文段落',recorded=True)
        return None

    def _skip_question(self, course, state, point_id, purpose, reason):
        if not isinstance(reason,dict):
            reason=dict(kind='question_program_error',code='QUESTION_UNAVAILABLE',reason=reason,recorded=True)
            log.error('point preparation program error: QUESTION_UNAVAILABLE')
        kind=reason['kind'];text=reason['reason']
        entry=dict(point_id=point_id,title=course.point(point_id)['title'],purpose=purpose,reason=text,
            kind=kind,code=reason['code'],day=self._day(state))
        gaps=state.setdefault('question_gaps',[])
        if not any(g['point_id']==point_id and g['purpose']==purpose and g['reason']==text for g in gaps):gaps.append(entry)
        if purpose=='final' and point_id not in state.setdefault('check_missing',[]):state['check_missing'].append(point_id)
        message=('这个知识点出题失败（程序问题，'+('已记录' if reason.get('recorded') else '证据保存失败')+'），本次未计分、未算通过。'
                 if kind=='question_program_error' else '找题暂未完成，请稍后再试；本次未计分、未算通过。')
        self._say(course,state,f"{entry['title']}：{message}",kind=kind)

    def _advance_missing(self, course, state, ctx, kind):
        if kind in ('module_test','skip_test','retest'):
            state['test']['index']+=1
            if state['test']['index']<len(state['test']['queue']):self._ask_next(course,state,ctx)
            else:self._finish_test(course,state,ctx)
        elif kind=='check':
            state['check']['index']+=1
            self._next_check(course,state,ctx)
        else:
            state['practice']['index']+=1
            self._ask_practice(course,state,ctx)



    def _show_question(self, course, state, q, kind, *, asks=1, number=None,total=None):
        q=copy.deepcopy(q)
        for field in ('source','answer_source'):
            if isinstance(q.get(field),str):
                try:q[field]=course.passage_card(q[field])
                except StopIteration:raise BridgeError('QUESTION_SOURCE_MISSING','这道题的原文段落已缺失，请补回资料。') from None
        self._materials().mark_used(course.store,course.plan_id,q['id'],PURPOSE[kind])
        state['pending']=dict(kind=kind,answer_id=uuid.uuid4().hex,point_ids=q['point_ids'],question=q,asks=asks,number=number,total=total)
        # Retain the previous verdict's explanation while showing the next card.
        state['pending']['question_message_id']=self._say(course,state,q['stem'],kind='question')

    def _explanation(self, course, q):
        source=q.get('answer_source',q['source']);text=q.get('explanation') or self._reference(q)
        return dict(text=text,source=source,label='资料解析' if q.get('explanation') else '参考答案原文')

    def why(self, course, state, ctx):
        if not state.get('answer_explanation'):raise BridgeError('STEP_INVALID','先回答一题，就可以看解析或答案出处。')
        state['answer_explanation']['open']=True

    def _settle(self, course, state, ctx, pending, verdict, text):
        kind=pending['kind'];q=pending['question'];context={'check':'check','practice':'practice'}.get(kind,kind)
        with course.store.transaction():
            if not pending.get('revealed'):self._user(course,state,text)
            if kind=='practice':
                if not any(r['id']==pending['answer_id'] for r in course.store.records('practice_results',course.plan_id)):
                    course.store.append_practice_result(course.plan_id,q['id'],course.point(q['point_ids'][0])['module_id'],q['point_ids'],verdict.correct,reason=verdict.reason,at=self.now(),result_id=pending['answer_id'],wrong_points=list(verdict.wrong_points))
            else:
                self._record(course,state,context,pending,verdict,text)
            self._answer_fact(course,state,context,pending,verdict)
            verdict_id=self._say(course,state,'✓ 答对了。' if verdict.correct else '✗ 这题没答对。',kind='verdict')
        explanation=self._explanation(course,q)
        state['answer_explanation']=dict(explanation,open=not verdict.correct,message_id=verdict_id)
        if not verdict.correct:self._say(course,state,state['answer_explanation']['text'],kind='answer_source',role='assistant')
        if ctx:ctx.stream(VERDICT_EVENT+json.dumps(dict(text='答对了' if verdict.correct else '这题没答对',correct=verdict.correct),ensure_ascii=False))
        state['pending']=None
        if kind in ('module_test','skip_test','retest'):
            test=state['test'];test['index']+=1
            if test['index']<len(test['queue']):self._ask_next(course,state,ctx)
            else:self._finish_test(course,state,ctx)
        elif kind=='check':
            state['check_used']=state.get('check_used',0)+1;state['check']['index']+=1
            self._next_check(course,state,ctx)
        else:
            practice=state['practice'];practice['index']+=1
            if practice['index']>=practice['count']:
                resume=practice['resume'];explanation=state.get('answer_explanation');state.clear();state.update(resume);state['answer_explanation']=explanation
                self._say(course,state,'这一轮自由检验完成了，计划与学习报告不变。')
            else:self._ask_practice(course,state,ctx)

    def _point_done(self, course, state):
        state.pop('concept',None)
        point=course.points_of(state['module_id'])[state['index']]
        self._credit_learning(course,state,point)
        state['index']+=1;state['pending']=None;state.pop('conversation',None)
        state['step']='teach' if state['index']<len(course.points_of(state['module_id'])) else 'test_offer'
        if state['step']=='test_offer' and not self._questions(course,state['module_id']) and self._ready(course,state['module_id']):
            for p in course.points_of(state['module_id']):course.store.confirm_credits(course.plan_id,p['id'],event_id=f"learn-only:{p['id']}",today=self._day(state))
            state['step']='test_done'
            self._say(course,state,'这个模块没有题，只学不考。')
            self.next_module(course,state,None)

    def start_test(self, course, state, ctx, kind):
        mid=state['module_id'];book=course.book();points=course.points_of(mid)
        if kind=='skip_test' and not self._can_direct(course,state):
            raise BridgeError('STEP_INVALID','请先完成当前答题，再直接考这个模块。')
        retryable_now=[q for q in self._questions(course,mid)
                       if (self._question_state(book,q['id']) is not None
                           and self._question_state(book,q['id']).module_correct is False)]
        retry_module_test=(kind=='module_test' and state['step']=='test_done'
                           and book.module(mid).state=='failed' and not retryable_now)
        if kind=='module_test' and state['step']!='test_offer' and not retry_module_test:
            raise BridgeError('STEP_INVALID','模块还没讲完。')
        if kind=='retest' and (state['step']!='test_done' or book.module(mid).state!='failed'):
            raise BridgeError('STEP_INVALID','现在没有待重测的题。')
        queue=self._questions(course,mid)
        # Existing missed questions remain answerable even if the bank's
        # preparation metadata changed after the assessment was closed.
        retest_queue=([q for q in queue if (self._question_state(book,q['id']) is not None
                                            and self._question_state(book,q['id']).module_correct is False)]
                      if kind=='retest' else None)
        ready=bool(retest_queue) if kind=='retest' else self._ready(course,mid)
        if not ready:
            module=course.module(mid)
            # Before readiness metadata existed, persisted subjective-only
            # banks were already considered prepared. Keep their historical
            # direct-review fallback; a genuinely empty/unmarked bank still
            # waits for preparation.
            legacy_unscored=(kind=='skip_test'
                and module.get('material_preparation',{}).get('state') is None
                and not self._questions(course,mid)
                and any(q.get('module_id')==mid and q.get('kind') in ('short','essay')
                        and q.get('role','bank')=='bank'
                        for q in course.store.records('question_bank',course.plan_id)))
        else:
            legacy_unscored=False
        if not ready and not legacy_unscored:
            job=self._jobs.get((course.plan_id,mid))
            if not job or job['status'] in ('failed','cancelled','ready'):
                job=self._prepare(course,mid,retry=True)
            state['material_notice']=dict(module_id=mid,kind=kind,message='这个模块的题还在准备，好了再考。')
            return
        state.pop('material_notice',None)
        if kind=='retest':
            queue=retest_queue or []
        if not queue:
            if kind=='skip_test':
                untaught=[p for p in points if p['id'] not in course.taught()]
                pool=untaught if len(untaught)>=3 else points
                selected=self._rng.sample(pool,min(3,len(pool)))
                state['direct_review']=dict(attempt=uuid.uuid4().hex[:16],resume=self._direct_position(state),
                    points=[p['id'] for p in selected],index=0,question='',answered=False,responses={})
                state.update(step='direct_review',pending=None,test=None)
                state.pop('conversation',None)
                return self._ask_direct(course,state,ctx)
            if any(p['id'] not in course.taught() for p in points):raise BridgeError('STEP_INVALID','这个模块没有题，请先学完内容。')
            state.update(step='test_done',pending=None,test=None)
            return self.next_module(course,state,ctx)
        resume=self._direct_position(state) if kind=='skip_test' else None
        if resume:state.pop('conversation',None)
        attempt=uuid.uuid4().hex[:16]
        parent,assessment_ids=self._latest_module_assessment(course,mid) if kind=='retest' else (None,[])
        assessment_id=parent or attempt
        if kind!='retest':assessment_ids=[q['id'] for q in queue]
        elif not assessment_ids:assessment_ids=[q['id'] for q in queue]
        state.update(step='retesting' if kind=='retest' else 'testing',
            test=dict(kind=kind,attempt=attempt,queue=queue,index=0,targets=[q['id'] for q in queue],
                      question_ids=[q['id'] for q in queue],assessment_id=assessment_id,
                      assessment_question_ids=assessment_ids,resume=resume))
        self._ask_next(course,state,ctx)

    @staticmethod
    def _latest_module_assessment(course, module_id):
        """Find a stable parent ID and its frozen question IDs."""
        candidates=[]
        for row in course.store.records('attempt_closed',course.plan_id):
            event=row.get('event',{})
            if event.get('module_id')!=module_id or event.get('kind') not in ('module_test','skip_test'):
                continue
            details=row.get('details') or {}
            attempt_id=details.get('assessment_id') or event.get('attempt_id')
            ids=details.get('question_ids')
            if not isinstance(ids,list):ids=[]
            candidates.append((event.get('seq',0),attempt_id,ids))
        if not candidates:return None,[]
        _,attempt_id,ids=max(candidates,key=lambda row:row[0])
        if not ids:
            # Legacy event history can still reconstruct the fixed first-test
            # denominator from the questions actually answered in that attempt.
            ids=list(dict.fromkeys(row['event'].get('question_id')
                for row in course.store.records('judgements',course.plan_id)
                if row.get('event',{}).get('attempt_id')==attempt_id
                and row.get('event',{}).get('question_id')))
        return attempt_id,ids

    def _assessment_summary(self, course, test, book):
        ids=list(test.get('assessment_question_ids') or test.get('question_ids') or test.get('targets') or [])
        parent=test.get('assessment_id') or test.get('attempt')
        attempt_ids={parent,test.get('attempt')}
        for row in course.store.records('attempt_closed',course.plan_id):
            event=row.get('event',{});details=row.get('details') or {}
            if (event.get('module_id')==test.get('module_id') and event.get('kind')=='retest'
                    and details.get('assessment_id')==parent):
                attempt_ids.add(event.get('attempt_id'))
        latest={}
        for row in course.store.records('judgements',course.plan_id):
            event=row.get('event',{});qid=event.get('question_id')
            if (event.get('module_id')!=test.get('module_id') or qid not in ids
                    or event.get('attempt_id') not in attempt_ids
                    or event.get('context') not in ('module_test','skip_test','retest')):
                continue
            seq=event.get('seq',0)
            correct=all(result[1] for result in event.get('results',[]))
            if qid not in latest or seq>latest[qid][0]:latest[qid]=(seq,correct)
        correct=0
        for qid in ids:
            if qid in latest:
                correct+=latest[qid][1]
                continue
            state=self._question_state(book,qid)
            correct+=bool(state and state.module_correct is True)
        return correct,len(ids)

    @staticmethod
    def _direct_position(state):
        return {key:copy.deepcopy(state.get(key)) for key in ('module_id','index','step','phase','conversation','concept')}

    @staticmethod
    def _restore_direct(state,resume):
        state.update(copy.deepcopy(resume),pending=None,test=None)
        state.pop('direct_review',None)
        state.pop('answer_explanation',None)
        state.pop('recall_result',None)

    @staticmethod
    def _can_direct(course,state):
        return (state.get('phase','learning')=='learning' and not state.get('pending')
            and state['step'] in ('teach','opening','teaching','closing','test_offer','test_done')
            and course.book().module(state['module_id']).state not in ('passed','skipped'))

    def _ask_direct(self,course,state,ctx):
        review=state['direct_review'];pid=review['points'][review['index']]
        review['answered']=False
        if not course.passages([pid]):
            review.update(question='这个点的原文暂时缺失，无法随口问。请自行决定接着学还是跳过。',answered=True)
            if review['index']+1==len(review['points']):state['step']='direct_choice'
            return
        teaching=self._teacher(course,state,ctx,teacher_agent.DIRECT_REVIEW,[pid])
        review['question']=_link_citations(teaching.speech,{f'c{i}':cid for i,cid in enumerate(sorted(self._teaching_excerpts(course,state,[pid])),1)})

    def _direct_view(self,course,state):
        review=state.get('direct_review')
        if not review:return None
        pid=review['points'][review['index']]
        entries=[]
        for i in range(review['index']+1):
            saved=review.get('responses',{}).get(str(i),{})
            point_id=review['points'][i]
            current=i==review['index']
            # Old states have no responses; still show their current prompt.
            if not saved and not current:continue
            answered=review['answered'] if current else True
            entries.append(dict(point_id=point_id,title=course.point(point_id)['title'],number=i+1,
                question=saved.get('question',review['question'] if current else ''),
                answer=saved.get('answer',''),answered=answered,
                sources=[course.passage_card(cid) for cid in course.passages([point_id])] if answered else []))
        return dict(point_id=pid,title=course.point(pid)['title'],number=review['index']+1,total=len(review['points']),
            question=review['question'],answered=review['answered'],
            entries=entries,answer=review.get('responses',{}).get(str(review['index']),{}).get('answer',''),
            sources=[course.passage_card(cid) for cid in course.passages([pid])] if review['answered'] else [])

    def direct_next(self,course,state,ctx):
        review=state.get('direct_review')
        if state['step']!='direct_review' or not review or not review['answered']:
            raise BridgeError('STEP_INVALID','先回答这句，再对照原文。')
        review['index']+=1
        self._ask_direct(course,state,ctx)

    def direct_choice(self,course,state,ctx,skip):
        if type(skip) is not bool or state['step']!='direct_choice' or not state.get('direct_review'):
            raise BridgeError('STEP_INVALID','随口聊完后，再选择跳过还是接着学。')
        if not skip:return self._restore_direct(state,state['direct_review']['resume'])
        self._finish_direct_choice(course,state,ctx)

    def _finish_direct_choice(self,course,state,ctx):
        review=state['direct_review'];mid=state['module_id']
        with course.store.transaction():
            self._direct_credits(course,state,review['attempt'])
            if not course.book().attempt_closed(mid,review['attempt']):
                # This is only the explicit skip decision. No scored answer is
                # invented from the preceding unrecorded conversation.
                course.store.append_attempt_closed(course.plan_id,AttemptClosed(
                    f"direct-choice:{review['attempt']}",course.next_seq(),mid,review['attempt'],'skip_test'))
        state.pop('direct_review',None)
        state.update(step='test_done',pending=None,test=None)
        self._say(course,state,'你选择跳过这个模块，已记入整模块学习时长；随口问不计分。',message_id=f"direct-choice:{review['attempt']}:message")
        self._publish_today(course,state,ctx)
        self._profile(course)

    def _direct_credits(self,course,state,attempt):
        live=course.store.load_ledger(course.plan_id).to_dict()['credits']
        history=course.store.records('credits',course.plan_id)
        for point in course.points_of(state['module_id']):
            credited=sum(c['minutes'] for c in live if c['unit_id']==point['id'])
            course.store.confirm_credits(course.plan_id,point['id'],event_id=f"confirm-direct:{attempt}:{point['id']}",today=self._day(state))
            missing=max(0,point['minutes']-credited)
            if missing:
                has_history=any(row['credit']['unit_id']==point['id'] for row in history)
                if not has_history:
                    # Keep the established fresh-learning ID when direct skip
                    # awards a point that has never had any learning credit.
                    course.store.add_credit(course.plan_id,f"learn:{point['id']}",point['id'],
                                            missing,self._day(state),'confirmed')
                else:
                    token=hashlib.sha256(point['id'].encode('utf-8')).hexdigest()
                    course.store.add_restored_learning(course.plan_id,
                        f"relearn:direct:{attempt}:{token}",point['id'],missing,self._day(state),
                        allow_current_remainder=True)

    def _restore_concept_learning(self,course,state,attempt):
        rows=course.store.records('credits',course.plan_id)
        live=course.store.load_ledger(course.plan_id).to_dict()['credits']
        for point in course.points_of(state['module_id']):
            if point.get('structure')!='concept_v1' or not point.get('subpoints'):
                continue
            historically_earned=any(row['credit']['unit_id']==point['id'] and
                                    row['id'].startswith(('subpoint:','learn:','relearn:'))
                                    for row in rows)
            if not historically_earned:
                continue
            credited=sum(item['minutes'] for item in live if item['unit_id']==point['id'])
            missing=max(0,point['minutes']-credited)
            if missing:
                token=hashlib.sha256(point['id'].encode('utf-8')).hexdigest()
                course.store.add_restored_learning(course.plan_id,
                    f"relearn:concept:{attempt}:{token}",point['id'],missing,self._day(state))

    def _ask_next(self, course, state, ctx=None):
        test=state['test']
        if test['index']<len(test['queue']):
            self._show_question(course,state,test['queue'][test['index']],test['kind'],number=test['index']+1,total=len(test['queue']))
        else:self._finish_test(course,state,ctx)

    def start_check(self, course, state, ctx):
        if state['step'] not in ('check_offer','checking') or state.get('pending'):raise BridgeError('STEP_INVALID','请先完成当前步骤。')
        if not course.book().check_ready():
            # A stale check offer recovers to a concrete module action instead
            # of hiding behind preparation or a generic failure.
            state.update(phase='learning',step='test_done',pending=None,test=None)
            return self.next_module(course,state,ctx)
        state.pop('material_notice',None)
        if course.plan['state']=='active':
            course.store.start_checking(course.plan_id);course.plan=course.store.get_plan(course.plan_id)
        state.setdefault('check_session',uuid.uuid4().hex)
        state.update(phase='check',step='checking',pending=None,check=None,check_used=0,check_missing=[])
        self._next_check(course,state,ctx)

    def _next_check(self, course, state, ctx):
        book=course.book()
        if book.check_complete():return self._complete(course,state,ctx)
        if state.get('check_used',0)>=CHECK_BATCH:
            state['check']=None;return self._say(course,state,'这组已答完，点继续检验开始下一组。')
        ids=book.next_check_questions(min_gap=3)
        bank={q['id']:q for q in self._questions(course)}
        ids=[qid for qid in ids if qid in bank]
        if not ids:
            # A changed bank may invalidate a saved selector result. Recover
            # from the actual current unresolved pool before showing a blocker.
            ids=[qid for qid in bank if (self._question_state(book,qid) is not None
                                         and not self._question_state(book,qid).passed)]
        if not ids:
            pending=next((m['id'] for m in course.modules if not self._ready(course,m['id'])),None)
            if pending is not None:
                job=self._jobs.get((course.plan_id,pending))
                if not job or job['status'] in ('failed','cancelled','ready'):
                    try:self._prepare(course,pending,retry=True)
                    except Exception:log.exception('final check preparation retry failed for %s/%s',course.plan_id,pending)
                state['material_notice']=dict(module_id=pending,kind='check',
                    message='最终检验还缺题目；后台正在重试准备，随后点“继续检验”。')
            else:
                state['material_notice']=dict(module_id=state['module_id'],kind='check',
                    message='当前题库与检验进度不一致；点“继续检验”重新读取题目，或重新进入课堂。')
            state['check']=None
            return
        state['check']=dict(queue=[bank[ids[0]]],index=0)
        self._ask_check(course,state)

    def _ask_check(self, course, state):
        check=state['check'];q=check['queue'][check['index']]
        state['module_id']=q['module_id']
        state['index']=next(i for i,p in enumerate(course.points_of(state['module_id'])) if p['id']==q['point_ids'][0])
        self._show_question(course,state,q,'check')

    def start_practice(self, course, state, ctx, module_id=None, point_id=None, count=5):
        if type(count) is not int or not 1<=count<=50:raise BridgeError('PARAM_INVALID','题数须为1～50。')
        if state.get('practice'):raise BridgeError('STEP_INVALID','当前自由检验还没结束。')
        if module_id and module_id not in {m['id'] for m in course.modules}:raise BridgeError('PARAM_INVALID','模块不存在。')
        if point_id and point_id not in {p['id'] for p in course.points}:raise BridgeError('PARAM_INVALID','知识点不存在。')
        targets=[p['id'] for p in course.points if (not module_id or p['module_id']==module_id) and (not point_id or p['id']==point_id) and (course.plan['state']=='completed' or p['id'] in course.taught())]
        if not targets:raise BridgeError('STEP_INVALID','所选范围还没有学过。')
        state['practice_session']=uuid.uuid4().hex
        state['practice']=dict(resume=copy.deepcopy(state),targets=targets,index=0,count=count,asked=[])
        state.update(step='practicing',phase='practice',pending=None)
        self._ask_practice(course,state,ctx)

    def _ask_practice(self, course, state, ctx):
        practice=state['practice'];review=question_bank.review_questions(course.store.records('practice_results',course.plan_id))
        pool=[q for q in self._questions(course) if any(pid in practice['targets'] for pid in q['point_ids'])]
        if not pool:
            resume=practice['resume'];state.clear();state.update(resume)
            return self._say(course,state,'所选范围没有题，继续学习即可。')
        asked=practice.setdefault('asked',[])
        self._rng.shuffle(pool);pool.sort(key=lambda q:(q['id'] not in review,q['id'] in asked))
        q=pool[0];asked.append(q['id']);state['module_id']=q['module_id']
        pid=q['point_ids'][0];state['index']=next(i for i,p in enumerate(course.points_of(q['module_id'])) if p['id']==pid)
        self._show_question(course,state,q,'practice',number=practice['index']+1,total=practice['count'])

    def _teacher_context(self, course, state, director, point_ids, *, user='',opening=False,allow_plan=False):
        concept=self._concept(course,state)
        scoped=self._concept_context(course,state,point_ids)
        wrapping=bool(concept and (state.get('concept') or {}).get('wrapping'))
        plan,module=self._skeleton(course,state,point_ids)
        excerpts=self._teaching_excerpts(course,state,point_ids)
        # A concept turn is grounded in its current subpoint (or bounded notes
        # for wrap-up). Plan-wide retrieval is reserved for explicit questions
        # on legacy points so it cannot re-expand concept context.
        if allow_plan and not wrapping:
            available=self._related_passages(course,excerpts,user,
                max_chars=4000 if scoped else 16000,max_passages=3 if scoped else 12)
        else:available=excerpts
        document=(course.store.records('teacher_profile',course.plan_id) or [{}])[0].get('document',{})
        tctx=dict(plan=plan,module=module,prefs=self._prefs(course),profile=document.get('text','')[:6000],dialogue=self._dialogue(course,state),
            current_points=[dict(id=pid,title=course.point(pid)['title']) for pid in point_ids],
            director=director,user=user,excerpts=excerpts,plan_passages=available,opening=opening)
        # An append-only page can retain old source blocks. Concept requests
        # must start from their exact bounded source map each time.
        tctx['request_page']=None if scoped else self._teacher_request_page
        if scoped and not concept:
            point,sub,_,_=scoped
            director=(director+'\n本次只围绕小要点“'+sub['title']+'”回应；不要把它扩展为整个大知识点。')
            tctx['director']=director
        if concept:
            point,sub,index=concept
            wrapping=(state.get('concept') or {}).get('wrapping',False)
            wrap_material=self._concept_wrap_material(point,index) if wrapping else None
            tctx['concept']=dict(title=(wrap_material[3] if wrapping else point['title']),
                goal=(wrap_material[2] if wrapping else point.get('goal',point['summary'])),
                subpoint_id=sub['id'],title_current=sub['title'],number=index+1,total=len(point['subpoints']),
                completed_titles=[s['title'] for s in point['subpoints'][:index]],wrapping=wrapping,
                notes=(wrap_material[0] if wrapping else [n for n in sub.get('notes',[])]))
            if wrapping:
                tctx['concept'].update(title_current=wrap_material[5],completed_titles=wrap_material[4],
                    subpoint_titles=wrap_material[6],omitted_subpoint_titles=wrap_material[7],
                    omitted_note_sentences=wrap_material[8])
            saved=state.get('concept') or {}
            intros=[r for r in saved.get('intro_endings',[]) if r['subpoint_id']!=sub['id']]
            tctx['concept'].update(first_turn=(saved.get('rounds',0)==0 and not wrapping),
                recent_styles=[] if wrapping else saved.get('style_history',[]),
                previous_intro_ending=None if wrapping else intros[-1]['ending'] if intros else None)
        return tctx

    def _teacher(self, course, state, ctx, director, point_ids, *, user='',opening=False,allow_plan=False):
        tctx=self._teacher_context(course,state,director,point_ids,user=user,opening=opening,allow_plan=allow_plan)
        request=teacher_agent.teacher_request(tctx)
        selected=getattr(self._local,'prefetched',None)
        self._local.prefetched=None
        # Only a prediction that failed BEFORE this user action is restarted.
        # Adopting a pending prediction keeps its original send time/deadline.
        if selected and selected.get('failed_before_click'):
            selected['cancel'].set();selected=None
        if selected and selected['request']==request and not selected['cancel'].is_set():
            try:
                def consume_prefetch():
                    offset=0
                    while True:
                        if ctx and ctx.cancel.is_set():selected['cancel'].set();raise ModelCancelled()
                        if not self._operation_is_current():raise _SupersededOperation()
                        # A finished prediction is already parsed. Do not replay
                        # rejected draft chunks (or restart speech) on a cache hit.
                        if offset==0 and selected['done'] and not selected['error']:
                            self._stream_current(ctx,selected['result'][0].speech)
                            break
                        with selected['condition']:
                            chunks=selected['chunks'][offset:];offset+=len(chunks)
                            done=selected['done']
                            if not chunks and not done:selected['condition'].wait(.025)
                        for chunk in chunks:self._stream_current(ctx,chunk)
                        if done:break
                    if selected['error']:raise selected['error']
                    return selected['result']
                result=self._slow_call(course,ctx,consume_prefetch)
                log.info('classroom prefetch hit plan=%s ready=%s',course.plan_id,selected['done'])
            except ModelCancelled:raise
            except _SupersededOperation:raise
            except (ModelError, AgentFormatError):raise
            except Exception:
                log.info('classroom prefetch unavailable; generating current reply',exc_info=True)
                self._stream_current(ctx,RESET)
                emit=lambda delta:self._stream_current(ctx,delta)
                result=self._slow_call(course,ctx,lambda:self._generate_teacher(course.plan_id,tctx,request,ctx.cancel if ctx else None,emit))
        else:
            if selected:selected['cancel'].set()
            emit=lambda delta:self._stream_current(ctx,delta)
            result=self._slow_call(course,ctx,lambda:self._generate_teacher(course.plan_id,tctx,request,ctx.cancel if ctx else None,emit))
        if not self._operation_is_current():raise _SupersededOperation()
        teaching,cache_request,accepted=result
        if tctx.get('request_page') is not None:
            self._teacher_request_page.accept(cache_request[0],cache_request[1],teacher_agent._dialogue(tctx['dialogue']),teacher_agent.history_text(accepted))
        return teaching

    def _generate_teacher(self, plan_id, tctx, request, cancel, emit, *, prefetched=False):
        """Only model work here: no state, transcript, credits or cache writes."""
        concept=tctx.get('concept')
        original_request=request
        for attempt in range(2):
            if cancel and cancel.is_set():raise ModelCancelled()
            raw='';visible=''
            def on_text(delta):
                nonlocal raw,visible
                if cancel and cancel.is_set():raise ModelCancelled()
                raw+=delta
                body=teacher_agent.streaming_body(raw)
                if body!=visible:
                    if body.startswith(visible):emit(body[len(visible):])
                    else:emit(RESET);emit(body)
                    visible=body
            def ask():
                return self.services.gateway.complete('teacher',request[1],system=request[0],on_text=on_text,
                    cancel=cancel,plan_id=plan_id,max_tokens=4096)
            # BUG-105: thinking mode can end after thinking with no text; ask once more quietly.
            defer=getattr(self.services.gateway,'defer_errors',None)
            try:
                with (defer() if callable(defer) else contextlib.nullcontext()):result=ask()
            except ModelError as exc:
                if (exc.code!='MODEL_RESPONSE_INVALID' or visible
                        or getattr(exc,'diagnostics',{}).get('reason')!='empty_text_or_invalid_model'):raise
                from refactor.runtime_logging import event
                event('classroom',state='TEACHER_EMPTY_RETRY',count=attempt+1,code='PREFETCH' if prefetched else 'FOREGROUND',purpose='teacher')
                result=ask()
            if cancel and cancel.is_set():raise ModelCancelled()
            try:
                teaching=teacher_agent.parse_teaching(result.text,tctx)
                teacher_agent.validate_presentation(teaching.speech,tctx)
                cache_request=original_request if concept else request
                if visible!=teaching.speech:emit(RESET);emit(teaching.speech)
                return teaching,cache_request,teaching.speech if concept else result.text
            except AgentFormatError as exc:
                from refactor.runtime_logging import event
                event('classroom',state='TEACHER_REJECTED',reason=getattr(exc,'reason','PARSE_FAILED'),
                      count=attempt+1,code='PREFETCH' if prefetched else 'FOREGROUND',purpose='teacher')
                emit(RESET)
                if attempt==0:
                    request=(request[0],request[1]+([dict(role='assistant',content=result.text)] if concept else [])+[dict(role='user',content=f'上一条回复格式不合格：{exc}。用中文解释本步内容，不出计分题、不声称修改计划；开场才给选项。重新写一次。')])
                elif str(exc).startswith(('concept','presentation')):
                    kept=teacher_agent.concept_fallback(result.text,tctx)
                    kept_ctx=dict(tctx)
                    if concept:kept_ctx['concept']=dict(concept,rewrite_exhausted=True)
                    teaching=teacher_agent.parse_teaching(kept,kept_ctx)
                    emit(teaching.speech)
                    return teaching,original_request,teaching.speech
                else:raise

    @staticmethod
    def _related_passages(course, excerpts, user, *, max_chars=16000,max_passages=12):
        """Retrieve bounded source text for an interruption, not the whole book."""
        words=set(re.findall(r'[a-zA-Z][a-zA-Z0-9_-]{1,}',user.lower()))
        for part in re.findall(r'[\u3400-\u9fff]+',user):
            words.update(part[i:i+n] for n in (2,3,4) for i in range(len(part)-n+1))
        if not words:return dict(excerpts)
        ranked=[];seen=set(excerpts)
        # Fetch and score one point at a time; do not materialize the whole
        # plan's source corpus before deciding which small passages to attach.
        for point in course.points:
            for cid,row in course.passages([point['id']]).items():
                if cid in seen:continue
                seen.add(cid)
                text=row['text'];lower=text.lower()
                score=sum(len(word) for word in words if word in lower)
                if score:
                    ranked.append((-score,cid,text));ranked.sort()
                    if len(ranked)>12:ranked.pop()
        result=dict(excerpts);remaining=max_chars
        for _,cid,text in sorted(ranked):
            if remaining<=0 or len(result)-len(excerpts)>=max_passages:break
            limit=min(4000,remaining)
            # Preserve the legacy retrieval contract: a long paragraph without
            # sentence punctuation still contributes its bounded source prefix.
            # Whole-sentence selection is only for the concept wrap bundle.
            bounded=text[:limit]
            if bounded:
                result[cid]=bounded;remaining-=len(bounded)
        return result

    def _deliver(self, course, state, teaching, user=None, explained=False):
        mapping={f'c{i}':cid for i,cid in enumerate(sorted(self._teaching_excerpts(course,state,self._focus(course,state))),1)}
        with course.store.transaction():
            if user is not None:self._user(course,state,user)
            body=teacher_agent.strip_source_footer(teaching.speech)
            speech=_link_citations(body,mapping)
            if mapping and (body!=teaching.speech or not teaching.cited_chunks):
                speech+='\n\n看原文：'+' '.join(_link_citations(f'[{alias}]',mapping) for alias in mapping)
            self._say(course,state,speech,role='assistant',kind='teach')
            concept=self._concept(course,state)
            if concept:
                saved=state.setdefault('concept',dict(point_id=concept[0]['id'],subpoint_id=concept[1]['id'],rounds=0))
                if not saved.get('wrapping'):
                    style=teacher_agent.teaching_style(teaching.speech)
                    saved['style_history']=(saved.get('style_history',[])+[dict(style,subpoint_id=concept[1]['id'])])[-12:]
                    state['concept_recent_styles']=saved['style_history']
                    if saved.get('rounds',0)==0:
                        saved['intro_endings']=(saved.get('intro_endings',[])+[dict(subpoint_id=concept[1]['id'],ending=style['ending'])])[-8:]
                saved['rounds']=saved.get('rounds',0)+1
            if explained:
                document=(course.store.records('teacher_profile',course.plan_id) or [{}])[0].get('document',{})
                points=dict(document.get('teaching_points',{}));pid=self._focus(course,state)[0]
                points[pid]=dict(point_id=pid,title=course.point(pid)['title'],chunk_ids=list(teaching.cited_chunks or mapping.values()),at=self.now().isoformat())
                course.store.save_teacher_profile(course.plan_id,dict(document,teaching_points=points))

    def _user(self, course, state, text):
        action=getattr(self._local,'action',None)
        token=action[2] if action else ((state.get('pending') or {}).get('answer_id') or uuid.uuid4().hex)+':'+hashlib.sha256(text.encode()).hexdigest()
        identifier=uuid.uuid5(uuid.NAMESPACE_URL,course.plan_id+':user:'+token).hex
        self._say(course,state,text,role='user',message_id=identifier)

    def _say(self, course, state, text, *, kind='text',role='system',message_id=None):
        action=getattr(self._local,'action',None)
        if not message_id and action:
            message_id=uuid.uuid5(uuid.NAMESPACE_URL,course.plan_id+':'+action[2]+':'+role+':'+kind+':'+text).hex
        channel=self._channel(state)
        # Keep duplicate detection and timestamp assignment in the same write
        # transaction, without decoding the plan's conversation history.
        with course.store.transaction():
            if message_id:
                try:previous_message=course.store.get_message(course.plan_id,message_id)
                except StoreError as exc:
                    if exc.code!='NOT_FOUND':raise
                else:
                    if previous_message['module_id']==channel and not previous_message['compressed']:return message_id
            previous=course.store.messages(course.plan_id,channel,limit=1)
            at=self.now()
            if previous:at=max(at,datetime.fromisoformat(previous[-1]['at'])+timedelta(microseconds=1))
            return course.store.append_message(course.plan_id,channel,role,text,kind=kind,message_id=message_id,at=at)

    def _record(self, course, state, context, pending, verdict, answer):
        if self._prior_verdict(course,state,pending):return
        wrong=set(verdict.wrong_points);q=pending['question']
        attempt=(state.get('test') or {}).get('attempt') if context in ('module_test','skip_test','retest') else None
        event=Judgement(pending['answer_id'],course.next_seq(),self.now(),context,q.get('module_id') or course.point(pending['point_ids'][0])['module_id'],
            attempt,tuple((pid,pid not in wrong) for pid in pending['point_ids']),question_id=q['id'] if context not in ('casual','quick_confirm') else None)
        details=dict(question=q['stem'],question_id=q['id'],question_type=q['kind'],answer=answer,reason=verdict.reason,
            point_checks=list(verdict.checks),source=q['source'],answer_source=q.get('answer_source',q['source']))
        if q['kind']=='recall':details.update(ticked=pending.get('ticked',[]),reference=q['reference'],key_points=q['key_points'],self_correct=pending.get('self_correct'))
        course.store.append_judgement(course.plan_id,event,details=details,credit_day=self._day(state) if context=='check' else None)

    def _answer_id(self, course, state, pending):return pending['answer_id']

    def _answer_fact(self, course, state, context, pending, verdict):
        q=pending['question']
        # An already committed answer survives a later scene-save interruption.
        if any(r['id']==pending['answer_id'] for r in course.store.records('question_answers',course.plan_id)):return
        snapshot={k:copy.deepcopy(q[k]) for k in ('id','kind','objective_kind','stem','options','answer','accepted','tolerance','reference','key_points','explanation','source','answer_source') if k in q}
        if q.get('options'):snapshot['correct_options']=question_bank.grading_question(q)['answer']
        missed=[i for i in range(len(q.get('key_points',[]))) if i not in pending.get('ticked',[])]
        attempt=(state.get('test') or {}).get('attempt') if context in ('module_test','skip_test','retest') else state.setdefault(context+'_session',uuid.uuid4().hex)
        course.store.append_question_answer(course.plan_id,pending['answer_id'],module_id=course.point(pending['point_ids'][0])['module_id'],context=context,
            attempt_id=attempt,question_id=q['id'],point_ids=pending['point_ids'],angle=None,
            correct=verdict.correct,wrong_points=verdict.wrong_points,reason=verdict.reason,checks=verdict.checks,at=self.now(),
            answer=pending.get('learner_answer'),ticked=pending.get('ticked'),missed=missed if q['kind']=='recall' else None,
            self_correct=pending.get('self_correct'),question=snapshot,dialogue_channel=self._channel(state),question_message_id=pending.get('question_message_id'))

    def _credit_learning(self, course, state, point):
        if point.get('structure')=='concept_v1' and point.get('subpoints'):
            missing=self._learning_remainder(course,point)
            if missing:
                credit_id=f"learn:{point['id']}"
                if not any(row['id']==credit_id for row in course.store.records('credits',course.plan_id)):
                    course.store.add_credit(course.plan_id,credit_id,point['id'],missing,self._day(state),'tentative')
            return
        course.store.add_credit(course.plan_id,f"learn:{point['id']}",point['id'],point['minutes'],self._day(state),'tentative')

    @staticmethod
    def _subpoint_credit_id(point, subpoint):
        token=hashlib.sha256((point['id']+'\0'+subpoint['id']).encode('utf-8')).hexdigest()
        return f"subpoint:{token}"

    def _credit_subpoint(self, course, state, point, subpoint):
        """Record one completed concept subpoint once, attached to its point."""
        credit_id=self._subpoint_credit_id(point,subpoint)
        rows=course.store.records('credits',course.plan_id)
        # The outbox stores immutable submissions even if a later module-test
        # failure revokes their live ledger projection.  Checking that journal
        # before adding also makes scene-save retries across study days safe.
        if any(row['id']==credit_id for row in rows):
            return False
        # A scene restored from an older version may already have its full-point
        # marker while still carrying a concept cursor. Do not append child time
        # on top of that completed point.
        if any(row['credit']['unit_id']==point['id'] and
               (row['id']==f"learn:{point['id']}" or row['id'].startswith('relearn:'))
               for row in rows):
            return False
        course.store.add_credit(course.plan_id,credit_id,point['id'],subpoint['minutes'],self._day(state),'tentative')
        return True

    @classmethod
    def _learning_remainder(cls, course, point):
        """Whole-point minutes still needed after recorded subpoint credits.

        Credit submissions are immutable history, so a module-test revoke does
        not turn an already completed lesson into new work.  The whole-point
        marker (including legacy/relearn markers) likewise proves completion.
        """
        rows=course.store.records('credits',course.plan_id)
        if any(row['credit']['unit_id']==point['id'] and
               (row['id']==f"learn:{point['id']}" or row['id'].startswith("relearn:"))
               for row in rows):
            return 0
        prefix="subpoint:"
        minutes=sum(row['credit']['minutes'] for row in rows
                    if row['credit']['unit_id']==point['id'] and row['id'].startswith(prefix))
        return max(0,point['minutes']-minutes)

    def _profile(self, course):
        document=(course.store.records('teacher_profile',course.plan_id) or [{}])[0].get('document',{})
        taught=set(document.get('teaching_points',{}))
        skipped={p['id'] for m in course.modules if course.book().module(m['id']).state=='skipped' for p in course.points_of(m['id']) if p['id'] not in taught}
        latest={}
        for row in sorted(course.store.records('judgements',course.plan_id),key=lambda r:r['event']['seq']):
            for pid,correct in row['event']['results']:
                if not correct:latest[pid]=row['details'].get('reason','')
        facts='；'.join(course.point(pid)['title']+'曾答错：'+reason for pid,reason in latest.items())
        text=f"已讲{len(taught)}个知识点；另有{len(skipped)}个点直接考通过、没有讲（全计划{len(course.points)}个点）。"
        if facts:text+='已记录的作答事实：'+facts
        course.store.save_teacher_profile(course.plan_id,dict(document,text=text,preferences=self._prefs(course),
            weak_points=[dict(point_id=pid,note=reason) for pid,reason in latest.items()]))

    def _missing_gaps(self, plan_id):
        legacy = self._file(plan_id)  # Validate the id without requiring an active lesson.
        store = self.services.store
        plan = store.get_plan(plan_id)
        if not store.classroom_gaps_imported(plan_id):
            if store.classroom_position(plan_id) is None:
                self._load(_Course(store, plan))  # Preserve the legacy-position import path.
            legacy_gaps = legacy.read({}).get('question_gaps', [])
            if not isinstance(legacy_gaps, list):
                legacy_gaps = []
            return store.import_classroom_gaps(plan_id, legacy_gaps)
        record = store.classroom_position(plan_id)
        return list((record or {}).get('state', {}).get('question_gaps') or [])

    def _file(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        return AccountJSON(self.services.account_id, self.services.data_root, f"classroom/{plan_id}.json")


    @staticmethod
    def _test_already_closed(course, state):
        test = state.get("test")
        return bool(test and test.get("kind") in ("module_test", "skip_test", "retest") and test.get("attempt")
                    and course.book().attempt_closed(state["module_id"], test["attempt"]))


    def _course(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        store = self.services.store
        try:
            plan = store.require_material_plan(plan_id)
        except StoreError:
            raise BridgeError("PLAN_NOT_FOUND", "找不到这份计划。") from None
        if plan.get("state") not in ("active", "checking", "completed"):
            raise BridgeError("PLAN_NOT_STARTED", "这份计划还没开始，请先在新建计划里点“开始计划”。")
        return _Course(store, plan)


    def _today(self, course, state):
        # The same ledger projection feeds home and classroom, including the
        # portion of this pinned study day's credits used to repay earlier days.
        from datetime import time as clock
        settings = self.services.settings.load()
        hour, minute = map(int, settings["refresh_time"].split(":"))
        now = self.now().astimezone()
        day = state.get("day")
        from datetime import date
        return course.store.today_status(course.plan_id, now=now, refresh=clock(hour, minute), tz=now.tzinfo,
                                         classroom_day=date.fromisoformat(day) if day else None)

    def _publish_today(self, course, state, ctx):
        # Publish committed credits before a potentially slow next teacher turn.
        # This is UI data, not speech or another model request.
        if not ctx:return
        try:today=self._today(course,state)
        except Exception:
            log.warning('classroom progress projection unavailable',exc_info=True)
            return
        ctx.stream(TODAY_EVENT+json.dumps(today,ensure_ascii=False))


    @staticmethod
    def _channel(state):
        return None if state.get("phase") == "check" else state["module_id"]


    def _day(self, state):
        return state.get("day") or self.study_day().isoformat()


    def _target_met(self, course, state):
        try:
            today = self._today(course, state)
        except Exception:  # noqa: BLE001 - without a status the lesson just continues
            return False
        return bool(today and today["today_target"] > 0
                    and today["today_confirmed"] + today["today_tentative"] >= today["today_target"])


    @staticmethod
    def _module_resume(state):
        keys=('module_id','index','step','phase','conversation','concept','material_notice','material_wait')
        return {key:copy.deepcopy(state[key]) for key in keys if key in state}

    def _reconcile_module_resume(self, course, module_id, resume):
        resume=copy.deepcopy(resume)
        if (resume.get('phase','learning')!='learning'
                or resume.get('step') not in ('teach','opening','teaching','closing')
                or course.book().module(module_id).state in ('passed','skipped')):
            return resume
        points=course.points_of(module_id)
        index=resume.get('index',0)
        if not isinstance(index,int) or index<0:index=0
        taught=course.taught()
        if index<len(points) and points[index]['id'] in taught:
            # Another device may have advanced this saved module while this
            # device was learning elsewhere. Resume from its first unfinished
            # point without rebuilding the account-wide classroom location.
            index=next((i for i,point in enumerate(points) if point['id'] not in taught),len(points))
            resume.update(index=index,step=('teach' if index<len(points) else
                'test_done' if course.book().module(module_id).state=='failed' else 'test_offer'))
            for key in ('conversation','concept','material_wait','material_notice','answer_explanation',
                        'recall_result','preparation_error'):
                resume.pop(key,None)
        return resume

    def _restore_module_resume(self, course, state, resume):
        # A module change replaces its teaching scene. Preserve the shared
        # resume map while clearing only ephemeral state from the old module.
        for key in ('conversation','concept','material_notice','material_wait','pending','test','check',
                    'direct_review','answer_explanation','recall_result','failure','stems','preparation_error'):
            state.pop(key,None)
        state.update(self._reconcile_module_resume(course,resume['module_id'],resume))

    def next_module(self, course, state, ctx, module_id=None):
        book=course.book();taught=course.taught()
        states=state.setdefault('module_resumes',{})
        valid_states=('teach','opening','teaching','closing','test_offer','test_done')
        if state.get('pending') or state.get('direct_review') or state.get('phase') in ('check','practice') or state['step'] in ('testing','retesting','checking'):
            raise BridgeError('STEP_INVALID','请先完成当前答题。')
        if state['step']=='preparing_materials':
            wait=state.pop('material_wait',None)
            if wait:
                state['step']=wait['previous_step']
                if wait.get('action')=='start_test':
                    state['material_notice']=dict(module_id=wait['module_id'],kind=(wait.get('args') or ['module_test'])[0],
                        message='这个模块的题还在准备，好了再考。')
        notice=state.get('material_notice')
        if notice and notice.get('module_id') in {m['id'] for m in course.modules}:
            if (book.module(notice['module_id']).state in ('passed','skipped')
                    or self._ready(course,notice['module_id'])):
                state.pop('material_notice',None)
        if module_id is not None:
            if module_id not in {m['id'] for m in course.modules} or book.module(module_id).state in ('passed','skipped'):
                raise BridgeError('PARAM_INVALID','所选模块当前不可继续学习。')
            if module_id==state['module_id']:return
            states[state['module_id']]=self._module_resume(state)
            saved=states.get(module_id)
            if saved:self._restore_module_resume(course,state,saved)
            else:
                points=course.points_of(module_id)
                index=next((i for i,p in enumerate(points) if p['id'] not in taught),len(points))
                step=('teach' if index<len(points) else 'test_offer')
                if book.module(module_id).state=='failed':step='test_done'
                self._restore_module_resume(course,state,dict(module_id=module_id,index=index,step=step,phase='learning'))
                states[module_id]=self._module_resume(state)
            return

        # An unfinished module can be parked at its exact subpoint or offer
        # state while the learner works elsewhere. No lesson marker or credit
        # is written by this navigation action.
        if state['step']!='test_done' and state['step'] not in valid_states:
            raise BridgeError('STEP_INVALID','请先完成当前答题。')
        if state['step']=='test_done' and state.get('material_notice'):
            # Keep the preparation retry visible. Falling through to ordinary
            # module navigation would append the same generic blocker on every
            # Continue click when no other module remains.
            return
        if book.check_ready():
            if not self._questions(course) and all(self._ready(course,m['id']) for m in course.modules):
                return self._complete(course,state,ctx)
            if state['step']!='check_offer':
                state.update(step='check_offer',phase='learning',pending=None,test=None)
                notice=state.get('material_notice')
                if notice and notice.get('kind')!='check':state.pop('material_notice',None)
                self._say(course,state,'全部模块都学完或通过了。最终检验会考全部题；答错过的题需连续答对两遍。',role='system')
            return
        if state['step']=='test_done' and not state.get('material_notice'):
            after=[m for m in course.modules if book.module(m['id']).state=='untested'
                   and m['id'] not in state.get('skipped_modules',[])]
            failed=[m for m in course.modules if book.module(m['id']).state=='failed']
            if after:
                # Prefer a module that still has unlearned points over a
                # parked assessment offer, retaining plan order within each tier.
                after.sort(key=lambda m: 0 if any(p['id'] not in taught
                    for p in course.points_of(m['id'])) else 1)
                module=after[0];points=course.points_of(module['id'])
                index=next((i for i,p in enumerate(points) if p['id'] not in taught),len(points))
                state.update(module_id=module['id'],index=index,step='teach' if index<len(points) else 'test_offer',
                    phase='learning',pending=None,test=None,stems=[])
                states[module['id']]=self._module_resume(state)
                return
            if failed:
                decisions={m['id']:self._module_continuation(book,m['id'],self._questions(course,m['id']),
                    self._ready(course,m['id'])) for m in failed}
                retryable=[m for m in failed if decisions[m['id']]=='retest']
                target=(retryable or failed)[0]
                continuation=decisions[target['id']]
                changed=state['module_id']!=target['id'] or state['step']!='test_done'
                state.update(module_id=target['id'],index=len(course.points_of(target['id'])),step='test_done',
                             phase='learning',pending=None,test=None)
                # The test_done view exposes the real retest control. Explain
                # navigation only when it actually changed modules so repeated
                # continue clicks do not append the same blocking notice.
                count=sum(self._question_state(book,q['id']) is not None
                          and self._question_state(book,q['id']).module_correct is False
                          for q in self._questions(course,target['id']))
                if continuation=='retest' and count and changed:
                    self._say(course,state,f'已切到 {target["title"]}：下面可以重测 {count} 道答错的题。',role='system')
                elif continuation=='prepare':
                    job=self._jobs.get((course.plan_id,target['id']))
                    if not job or job['status'] in ('failed','cancelled','ready'):
                        try:self._prepare(course,target['id'],retry=True)
                        except Exception:log.exception('module assessment retry failed for %s/%s',course.plan_id,target['id'])
                    state['material_notice']=dict(module_id=target['id'],kind='module_test',
                        message=f'{target["title"]} 暂时没有可重测的现成题；正在重新准备，可继续其他步骤或点“重试准备”。')
                elif continuation=='module_test' and changed:
                    self._say(course,state,f'{target["title"]} 的检测状态需要重新核对；可重新检测本模块题目。',role='system')
                return
            # Keep the current learning channel visible through the offer;
            # start_check switches to the global check channel when testing
            # actually begins.
            state.update(step='check_offer',pending=None,test=None)
            self._say(course,state,'全部模块都学完或通过了。最终检验会考全部题；答错过的题需连续答对两遍。',role='system')
            return

        current=state['module_id']
        if state.get('material_notice') and state['step'] not in valid_states:
            raise BridgeError('STEP_INVALID','当前步骤不能切换模块。')
        lesson_candidates=[];assessment_candidates=[]
        for candidate in course.modules:
            mid=candidate['id']
            if mid==current or book.module(mid).state in ('passed','skipped'):continue
            points=course.points_of(mid)
            idx=next((i for i,p in enumerate(points) if p['id'] not in taught),len(points))
            resume=states.get(mid)
            if resume or idx<len(points) or book.module(mid).state=='failed':
                (lesson_candidates if idx<len(points) else assessment_candidates).append(candidate)
        candidates=lesson_candidates or assessment_candidates
        if not candidates:
            self._say(course,state,'没有其他可先学习的模块；当前模块保持原位置，题目备好后可继续检测。',role='system')
            return
        states[current]=self._module_resume(state)
        target=candidates[0]['id']
        saved=states.get(target)
        if saved:self._restore_module_resume(course,state,saved)
        else:
            points=course.points_of(target)
            index=next((i for i,p in enumerate(points) if p['id'] not in taught),len(points))
            step='teach' if index<len(points) else ('test_done' if book.module(target).state=='failed' else 'test_offer')
            self._restore_module_resume(course,state,dict(module_id=target,index=index,step=step,phase='learning'))
            states[state['module_id']]=self._module_resume(state)
        return


    def _complete(self, course, state, ctx):
        try:
            course.store.complete_plan(course.plan_id, today=self._day(state))
        except StoreError as exc:
            if exc.code != 'PLAN_NOT_COMPLETE': raise
            self._say(course, state, '还需要通过全部题的最终检验，才能完成计划。')
            return
        course.plan = course.store.get_plan(course.plan_id)
        state.update(step="completed", pending=None, check=None)
        self._say(course, state, "这份计划已完成！可以在「我的计划」里查看学习报告。")


    def _prefs(self, course):
        from .settings import teaching_preferences
        return teaching_preferences(self.services.settings, course.store, course.plan_id)


    def _skeleton(self, course, state, point_ids):
        module = course.module(state["module_id"])
        return ({"id": course.plan_id, "goal": course.plan.get("goal") or "学习", "final_goal": course.plan.get("final_goal") or "掌握",
                 "level": LEVELS.get(course.plan.get("level"), "未说明")},
                {"id": module["id"], "title": module["title"],
                 "points": [{"id": p["id"], "title": p["title"], "summary": p.get("summary", "")} for p in course.points_of(module["id"])]})


    def _dialogue(self, course, state, *, recap=None):
        rows = []
        for m in course.store.messages(course.plan_id, self._channel(state)):
            role = "user" if m["role"] == "user" else "assistant"
            text = m["content"] if m["role"] != "system" else f"（系统）{m['content']}"
            if m.get('kind')=='recap':
                try:
                    text=self._recap_text(json.loads(text))
                except (ValueError,KeyError,TypeError):pass
            rows.append({"role": role, "text": text})
        if recap is not None:rows.append(dict(role='assistant',text=self._recap_text(recap)))
        return dialogue_tail(rows, max_rows=DIALOGUE_ROWS, max_chars=DIALOGUE_CHARS)


    def _focus(self, course, state):
        if state.get('pending'):return state['pending']['point_ids']
        if state.get('direct_review'):
            review=state['direct_review']
            return [review['points'][review['index']]]
        points = course.points_of(state["module_id"])
        return [points[min(state["index"], len(points) - 1)]["id"]]


    def _prior_verdict(self, course, state, pending):
        answer_id = self._answer_id(course, state, pending)
        fact = next((r for r in course.store.records('question_answers', course.plan_id) if r['id'] == answer_id), None)
        if fact:
            return judge_agent.Verdict('answer', fact['correct'], tuple(fact['wrong_points']), fact['reason'],
                                       checks=tuple(fact['checks']))
        entity = 'practice_results' if pending.get('kind') == 'practice' else 'judgements'
        row = next((item for item in course.store.records(entity, course.plan_id)
                    if item['id'] == answer_id), None)
        if row is None: return None
        if entity == 'practice_results':
            return judge_agent.Verdict('answer', row['correct'],
                                       tuple(row.get('wrong_points', [] if row['correct'] else row['point_ids'])), row['reason'])
        event = row['event']
        return judge_agent.Verdict('answer', all(result[1] for result in event['results']),
                                   tuple(result[0] for result in event['results'] if not result[1]),
                                   row['details'].get('reason', ''), checks=tuple(row['details'].get('point_checks', ())),
                                   review=row['details'].get('judgement_review'))


    def _finish_test(self, course, state, ctx):
        test=state['test'];mid=state['module_id'];store=course.store;day=self._day(state)
        if not course.book().attempt_closed(mid,test['attempt']):
            details={'question_ids':list(test.get('question_ids') or test.get('targets') or []),
                     'assessment_id':test.get('assessment_id') or test['attempt']}
            store.append_attempt_closed(course.plan_id,
                AttemptClosed(uuid.uuid4().hex,course.next_seq(),mid,test['attempt'],test['kind']),details=details)
        with store.transaction():
            store.settle_module_test(course.plan_id,mid,test['attempt'],today=day)
            book=course.book();passed=book.module(mid).state in ('passed','skipped')
            if passed and book.module(mid).state=='skipped':
                self._direct_credits(course,state,test['attempt'])
            elif passed or test['kind']=='module_test':
                for p in course.points_of(mid):
                    is_concept=p.get('structure')=='concept_v1' and p.get('subpoints')
                    if test['kind']=='module_test' and not passed and is_concept:
                        store.revoke_credits(course.plan_id,p['id'],event_id=f"revoke-concept:{test['attempt']}:{p['id']}",today=day)
                    else:
                        store.confirm_credits(course.plan_id,p['id'],event_id=f"confirm:{test['attempt']}:{p['id']}",today=day)
                if passed and test['kind']=='retest':
                    self._restore_concept_learning(course,state,test['attempt'])
        test['module_id']=mid
        correct,total=self._assessment_summary(course,test,book)
        state.update(step='test_done',pending=None,test=None)
        if not passed and test['kind']=='skip_test' and test.get('resume'):
            explanation=state.get('answer_explanation')
            self._restore_direct(state,test['resume'])
            if explanation:state['answer_explanation']=explanation
        self._say(course,state,f'本模块答对 {correct}/{total} 道题。'+('已达到90%，模块通过。' if passed else '尚未达到90%，回到直接考之前的位置接着学。' if test['kind']=='skip_test' else '尚未达到90%，只重测答错的题，不重讲。'))
        self._publish_today(course,state,ctx)
        self._profile(course)

class _Course:
    """One read of a plan's structure for the duration of an operation."""

    def __init__(self, store, plan):
        self.store, self.plan, self.plan_id = store, plan, plan["id"]
        self.modules = sorted(store.records("modules", self.plan_id), key=lambda m: (m["position"], m["id"]))
        position = {m["id"]: i for i, m in enumerate(self.modules)}
        self.points = sorted(store.records("points", self.plan_id),
                             key=lambda p: (position.get(p["module_id"], 0), p["position"], p["id"]))
        for point in self.points:
            point.setdefault("title", (point.get("summary") or "知识点")[:40])
        self._points_by_id={p['id']:p for p in self.points}
        self._points_by_module={m['id']:[] for m in self.modules}
        for point in self.points:self._points_by_module[point['module_id']].append(point)
        self._chunks = None
        self._sources = None
        if not self.modules or not self.points:
            raise BridgeError("PLAN_EMPTY", "这份计划还没有可学的内容。")

    def module(self, module_id):
        return next(m for m in self.modules if m["id"] == module_id)

    def points_of(self, module_id):
        return self._points_by_module.get(module_id,[])

    def point(self, point_id):
        try:return self._points_by_id[point_id]
        except KeyError:raise StopIteration from None

    def book(self):
        return self.store.load_mastery(self.plan_id)

    def next_seq(self):
        """Replay order for mastery events: after every recorded event, even if the clock went back."""
        seqs = [row["event"]["seq"] for entity in ("judgements", "attempt_closed")
                for row in self.store.records(entity, self.plan_id)]
        return max([time.time_ns() // 1000, *(seq + 1 for seq in seqs)])

    def taught(self):
        return self.store.learning_credit_projection(self.plan_id)['taught']

    def passages(self, point_ids):
        """Point chunk_ids are the projection of point_sources, never a guessed source."""
        chunks,sources=self._source_rows()
        wanted=list(dict.fromkeys(cid for pid in point_ids for cid in self.point(pid).get('chunk_ids',[])))
        return {cid:chunks[cid] for cid in wanted if cid in chunks and chunks[cid].get('text','').strip()
                and sources.get(chunks[cid]['source_id'],{}).get('adopted')}

    def excerpts(self, point_ids):
        result={cid:row['text'][:40000] for cid,row in self.passages(point_ids).items()}
        if not result:raise BridgeError('NO_MATERIAL','这个知识点没有关联到已采用的原文，请在计划里补充资料。')
        return result

    def passage_card(self, chunk_id):
        chunks,sources=self._source_rows()
        try:chunk=chunks[chunk_id];source=sources[chunk['source_id']]
        except KeyError:raise StopIteration from None
        return dict(id=chunk_id,text=chunk['text'],title=source.get('title') or source.get('filename') or '资料',
                    url=source.get('url'),locator=' / '.join(chunk.get('section') or []) or f"段落 {chunk.get('position',0)+1}")

    def _source_rows(self):
        # This course instance lives for one operation. Reopening or a material
        # preparation reload creates a fresh instance, so later edits are seen.
        if self._chunks is None:
            self._chunks={c['id']:c for c in self.store.records('source_chunks',self.plan_id)}
            self._sources={s['id']:s for s in self.store.records('sources',self.plan_id)}
        return self._chunks,self._sources


def _link_citations(text, mapping):
    """[c3] -> a link the page opens as the original source text."""
    numbers = {}

    def link(match):
        chunk = mapping.get(match.group(1).lower())
        if not chunk:
            return ""
        n = numbers.setdefault(chunk, len(numbers) + 1)
        return f"[〔{n}〕](mf-source:{chunk})"
    literal = re.compile(r"```.*?```|`[^`\n]+`", re.S)
    out, end = [], 0
    for m in literal.finditer(text):
        out.append(re.sub(r"\[([cC]\d+)\]", link, text[end:m.start()]))
        out.append(m.group())
        end = m.end()
    out.append(re.sub(r"\[([cC]\d+)\]", link, text[end:]))
    return "".join(out)


def register(bridge, services, study_day, *, presence=lambda active,plan_id=None:None, now=_now):
    rooms={}
    def room():
        current=services();key=id(current)
        if key not in rooms:
            for old in rooms.values():old.cancel_materials()
            rooms.clear()
            rooms[key]=Classroom(current,study_day,now=now)
            rooms[key].material_event=lambda data:bridge.push('classroom.materials_ready',data)
        return rooms[key]
    def text(p):
        value=p.get('text')
        if not isinstance(value,str) or not value.strip() or len(value)>4000:raise BridgeError('PARAM_INVALID','内容不能为空或超过4000字。')
        return value.strip()
    def invoke(action,params,ctx,*args):return room().perform(params.get('plan_id'),action,ctx,args,params.get('request_id'))
    def test(p,c):
        kind=p.get('kind')
        if kind not in ('module_test','skip_test','retest'):raise BridgeError('PARAM_INVALID','检测类型无效。')
        return invoke('start_test',p,c,kind)
    def enter(p,c):
        result=invoke('enter',p,c);presence(True,p.get('plan_id'));return result
    def leave(p,c):
        room().cancel_prefetch(p.get('plan_id'))
        result=invoke('leave',p,c);presence(False);return result
    def source(p,c):
        co=room()._course(p.get('plan_id'));cid=p.get('chunk_id')
        if cid not in co.passages([pt['id'] for pt in co.points]):raise BridgeError('NOT_FOUND','找不到已采用且关联的这段原文。')
        card=co.passage_card(cid)
        return dict(card,section=[card['locator']])
    def reveal(p,c):
        result=invoke('recall_reveal',p,c,p.get('question_id'),p.get('answer'))
        if result.get('failure'):return result
        q=result.get('question') or {}
        return dict(result,reference=q.get('reference'),key_points=q.get('key_points'),
                    key_point_headings=q.get('key_point_headings'),ticked=q.get('ticked'))
    def submit(p,c):
        result=invoke('recall_submit',p,c,p.get('question_id'),p.get('ticked'),p.get('correct'))
        if result.get('failure'):return result
        return dict(result,**(result.get('recall_result') or {}))
    methods={
        'class.recall_reveal':reveal,'class.recall_submit':submit,
        'class.recall_draft':lambda p,c:invoke('recall_draft',p,c,p.get('question_id'),p.get('ticked')),
        'classroom.wrong_answers':lambda p,c:room().services.store.wrong_answers(p.get('plan_id')),
        'classroom.enter':enter,'classroom.leave':leave,'classroom.source':source,
        'classroom.state':lambda p,c:room().run(p.get('plan_id'),lambda *args:None,c,readonly=True),
        'classroom.next':lambda p,c:invoke('next',p,c),
        'classroom.skip_point':lambda p,c:invoke('skip_point',p,c),
        'classroom.prefetch':lambda p,c:room().prefetch(p.get('plan_id'),p.get('version')),
        'classroom.cancel_prefetch':lambda p,c:room().cancel_prefetch(p.get('plan_id'),p.get('version')) or dict(ok=True),
        'classroom.choose':lambda p,c:invoke('choose',p,c,p.get('option')),
        'classroom.confused':lambda p,c:invoke('confused',p,c),
        'classroom.ask':lambda p,c:invoke('ask',p,c,text(p)),
        'classroom.reply':lambda p,c:invoke('reply',p,c,text(p)),
        'classroom.answer':lambda p,c:invoke('answer',p,c,p.get('value')),
        'classroom.why':lambda p,c:invoke('why',p,c),
        'classroom.back':lambda p,c:invoke('back',p,c),
        'classroom.retry':lambda p,c:invoke('retry',p,c),
        'classroom.cancel_preparation':lambda p,c:invoke('stop_preparation',p,c),
        'classroom.gaps':lambda p,c:room()._missing_gaps(p.get('plan_id')),
        'classroom.test':test,
        'classroom.direct_next':lambda p,c:invoke('direct_next',p,c),
        'classroom.direct_choice':lambda p,c:invoke('direct_choice',p,c,p.get('skip')),
        'classroom.next_module':lambda p,c:invoke('next_module',p,c,p.get('module_id')),
        'classroom.check':lambda p,c:invoke('start_check',p,c),
        'classroom.practice':lambda p,c:invoke('start_practice',p,c,p.get('module_id'),p.get('point_id'),p.get('count',5)),
    }
    for name,fn in methods.items():bridge.register(name,fn)
