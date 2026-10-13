"""Grounded conversational classroom. Questions are selected, never generated."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
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
from ..course.mastery import AttemptClosed, Judgement
from ..course.planner_agents import LEVELS, AgentFormatError
from ..course.store import StoreError
from ..preferences.storage import AccountJSON, StorageError
from .bridge import BridgeError, CallContext

log = logging.getLogger(__name__)
_PLAN_ID = re.compile(r"[a-f0-9]{32}")
PREF_KEYS = ("teacher_style", "detail", "examples", "analogy_domains")
DIALOGUE_ROWS, DIALOGUE_CHARS, CHECK_BATCH = 40, 60000, 4
RESET, VERDICT_EVENT = "\x00", "\x01VERDICT:"
MATERIAL_EVENT = "\x01MATERIAL:"
TODAY_EVENT = "\x01TODAY:"
PURPOSE = {"module_test": "module", "skip_test": "direct",
           "retest": "retest", "check": "final", "practice": "free"}


def _now():
    return datetime.now(timezone.utc)


class Classroom:
    _mutex = threading.Lock()
    _room_locks = {}

    def __init__(self, services, study_day, *, now=_now, rng=None):
        self.services, self.study_day, self.now = services, study_day, now
        self._rng = rng or random.Random()
        self._jobs, self._entered_plans = {}, set()
        self._local = threading.local()
        self.material_event = lambda data: None
        self._teacher_request_page = AppendOnlyPage()

    def cancel_materials(self, plan_id=None):
        """Only this classroom's preparation jobs, never the shared gateway/store."""
        for key,job in list(self._jobs.items()):
            if plan_id is None or key[0]==plan_id:
                job['cancel'].set();self._jobs.pop(key,None)

    def _activity_key(self, plan_id):
        return (str(self.services.data_root), self.services.account_id, plan_id)

    def _lock(self, plan_id):
        key = self._activity_key(plan_id)
        with self._mutex:
            return self._room_locks.setdefault(key, threading.Lock())

    def perform(self, plan_id, action, ctx, args=(), request_id=None):
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[\w-]{1,100}", request_id)):
            raise BridgeError("PARAM_INVALID", "请求编号无效。")
        self._local.action = (action, list(args), request_id or uuid.uuid4().hex)
        try:
            return self.run(plan_id, lambda co, st, cx: getattr(self, action)(co, st, cx, *args), ctx)
        finally:
            self._local.action = None

    def run(self, plan_id, work, ctx=None):
        lock = self._lock(plan_id)
        if not lock.acquire(False):
            message = "课堂正在处理，请稍后再发；已有内容和位置保留。"
            raise BridgeError("CLASS_BUSY", message)
        try:
            course = self._course(plan_id)
            state = self._load(course)
            action = getattr(self._local, "action", None)
            if action and action[2] in state.get("completed_requests", []):
                return self.snapshot(course, state)
            original = copy.deepcopy(state)
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
                            self._prepare_next_module(course, state, entering=bool(action and action[0]=='enter'))
                state.pop("failure", None)
                if action:
                    retried=getattr(self._local,'action',None)
                    state["completed_requests"] = (state.get("completed_requests", []) + [action[2]] + ([retried[2]] if retried else []))[-40:]
            except ModelCancelled:
                if ctx: ctx.stream(RESET)
                self._save(course, original)
                raise BridgeError("CANCELLED", "已停止，当前题目和位置保留。") from None
            except (ModelError, AgentFormatError, StoreError, BridgeError) as exc:
                state = original
                state["failure"] = dict(message=getattr(exc, "message", str(exc)),
                    action=action[0] if action else "next", args=action[1] if action else [],
                    request_id=action[2] if action else uuid.uuid4().hex)
                if ctx: ctx.stream(RESET)
            except Exception:
                log.exception("classroom operation failed for %s", plan_id)
                state = original
                state["failure"] = dict(message="这一步没有完成，请重试；原来的题目和位置保留。",
                    action=action[0] if action else "next", args=action[1] if action else [],
                    request_id=action[2] if action else uuid.uuid4().hex)
                if ctx: ctx.stream(RESET)
            self._save(course, state)
            return self.snapshot(course, state)
        finally:
            lock.release()

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
            messages.append(dict(id=m['id'],role=m['role'],kind=m.get('kind','text'),content=m['content'],sources=cards))
        question = {k:q.get(k, []) if k=='options' else q.get(k) for k in ('id','kind','stem','options','source')} if q else None
        if question and q['kind']=='objective':
            graded=question_bank.grading_question(q);question['question_type']=graded['kind']
            if graded['kind']=='fill':question['blanks']=len(graded['answer'])
        if question and q['kind']=='recall':
            question['revealed']=bool(pending.get('revealed'))
            if isinstance(question.get('source'),dict):
                question['source']={k:v for k,v in question['source'].items() if k!='text'}
            if pending.get('revealed'):question.update(reference=q['reference'],key_points=q['key_points'],answer=pending['learner_answer'],ticked=pending.get('ticked',[]))
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
        preparing = self._jobs.get((course.plan_id, module['id']))
        current_preparation = ({k:preparing[k] for k in ('module_id','done','total','text','error')}
            if preparing and preparing['status'] in ('running','queued','failed') and not preparing['cancel'].is_set()
            and state.get('phase')=='learning' else None)
        return dict(plan={k:course.plan.get(k) for k in ('id','goal','state')},
            module=dict(id=module['id'],title=module['title'],number=course.modules.index(module)+1,total=len(course.modules),state=book.module(module['id']).state,navigation=navigation),
            points=[dict(id=p['id'],title=p['title'],minutes=p['minutes'],taught=p['id'] in taught,state=book.point(p['id']).state,current=i==state['index']) for i,p in enumerate(points)],
            step=state['step'],question=question,messages=messages,conversation=state.get('conversation'),concept=concept_view,
            preparation=state.get('material_wait') or current_preparation,failure=state.get('failure'),explanation=state.get('answer_explanation'),
            can_skip=self._can_direct(course,state),
            direct_review=self._direct_view(course,state),
            red=[p['title'] for p in points if book.point(p['id']).state=='red'],
            more_modules=any(book.module(m['id']).state=='untested' for m in course.modules if m['id']!=module['id']),
            recall_result=state.get('recall_result'),
            check=dict(passed=sum(book.question(q['id']).passed for q in self._questions(course)),total=len(self._questions(course))) if state.get('phase')=='check' or state['step'] in ('check_offer','completed') else None,today=today)

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

    def _wait(self, course, state, action, args=()):
        mid=state['module_id'];job=self._prepare(course,mid)
        state['material_wait']=dict(module_id=mid,action=action,args=list(args),previous_step=state['step'],done=job['done'],total=job['total'],text=job['text'],error=job['error'])
        state['step']='preparing_materials'

    def _resume_materials(self, course, state, ctx):
        wait=state.get('material_wait')
        if not wait:return
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
        if (course.book().check_complete() and not self._questions(course) and course.plan['state']!='completed'
                and all(self._ready(course,m['id']) for m in course.modules)):
            return self._complete(course,state,ctx)
        state.pop('materials_paused',None)
        if course.plan_id not in self._entered_plans or not state.get('in_session'):
            state['day']=self.study_day().isoformat()
        self._entered_plans.add(course.plan_id);state['in_session']=True

    def leave(self, course, state, ctx):
        self._entered_plans.discard(course.plan_id)
        self.cancel_materials(course.plan_id)
        state['in_session']=False;state['day']=None
        self._profile(course)

    def retry(self, course, state, ctx):
        state.pop('materials_paused',None)
        failure=state.pop('failure',None)
        if failure:
            self._local.action=(failure['action'],failure['args'],failure['request_id'])
            return getattr(self,failure['action'])(course,state,ctx,*failure['args'])
        wait=state.get('material_wait')
        if wait:
            job=self._jobs.pop((course.plan_id,wait['module_id']),None)
            if job:job['cancel'].set()
            self._prepare(course,wait['module_id'],retry=True)
        elif (self._jobs.get((course.plan_id,state['module_id'])) or {}).get('status')=='failed':
            self._prepare(course,state['module_id'],retry=True)

    def stop_preparation(self, course, state, ctx):
        state['materials_paused']=True
        self.cancel_materials(course.plan_id)
        wait=state.pop('material_wait',None)
        if wait:state['step']=wait['previous_step']

    def next(self, course, state, ctx):
        state.pop('materials_paused',None)
        if state['step']=='preparing_materials':return
        concept=self._concept(course,state)
        if concept and state['step'] in ('teaching','closing'):
            point,sub,index=concept
            if index+1<len(point['subpoints']):
                return self._concept_begin(course,state,ctx,index+1)
            state.setdefault('concept',dict(point_id=point['id'],subpoint_id=sub['id'],rounds=0))['wrapping']=True
            teaching=self._teacher(course,state,ctx,'这个大知识点的小要点已经讲完。用一两句把它们与掌握目标串起来收束，不再出问题，不重讲全文。',[point['id']])
            self._deliver(course,state,teaching)
            state.pop('concept',None)
            self._point_done(course,state)
            self._publish_today(course,state,ctx)
            return
        if state['step'] in ('teaching','closing'):
            self._point_done(course,state)
            self._publish_today(course,state,ctx)
            if state['step']!='teach':return
            if self._target_met(course,state):return
        if state['step']!='teach':raise BridgeError('STEP_INVALID','请先完成当前的对话或题目。')
        if not self._ready(course,state['module_id']) and state.get('materials_attempted',{}).get(state['module_id'])!=self._fingerprint(course,state['module_id']):return self._wait(course,state,'next')
        point=course.points_of(state['module_id'])[state['index']]
        if not course.passages([point['id']]):
            self._prepare_one(course,point['id'],ctx)
            course = self._course(course.plan_id)
            if not course.passages([point['id']]):
                self._skip_question(course,state,point['id'],'learning','没有关联的原文段落')
                state['index']+=1
                state['step']='teach' if state['index']<len(course.points_of(state['module_id'])) else 'test_offer'
                return
        if point.get('structure')=='concept_v1' and point.get('subpoints'):
            return self._concept_begin(course,state,ctx,0)
        chapter=f"先用一句话说明这一章要解决什么问题：{course.module(state['module_id'])['title']}。" if state['index']==0 else ''
        teaching=self._teacher(course,state,ctx,chapter+'用一个生活化问题开头，给2～3个不计分的可点选项，也允许打字。先不要讲完整结论。',[point['id']],opening=True)
        self._deliver(course,state,teaching)
        options=list(teaching.options)
        if '直接开始讲' not in options:options.append('直接开始讲')
        state.update(step='opening',conversation=dict(point_id=point['id'],options=options,interrupted=False))

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
        state['concept']=dict(point_id=point['id'],subpoint_id=sub['id'],rounds=0)
        teaching=self._teacher(course,state,ctx,'现在开始当前小要点。结合学生刚才的回应和最近的开场，自行选择生活化问题、短例子、对比或从上一小要点接过来，不按序号轮换套路，不连续套同一模板。不一次讲完全部小要点。围绕当前讲义展开，留空间让学生回应；不必每轮结尾问问题，不输出选项块。',[point['id']])
        self._deliver(course,state,teaching,explained=True)
        state.update(step='teaching',conversation=dict(point_id=point['id'],options=[],interrupted=False))

    def _teaching_excerpts(self,course,state,point_ids):
        excerpts=course.excerpts(point_ids)
        concept=self._concept(course,state)
        if concept and point_ids==[concept[0]['id']] and not (state.get('concept') or {}).get('wrapping'):
            wanted=set(concept[1]['chunk_ids'])
            return {cid:text for cid,text in excerpts.items() if cid in wanted}
        return excerpts

    def choose(self, course, state, ctx, option):
        conversation=state.get('conversation') or {}
        options=conversation.get('options') or []
        if state['step']!='opening' or type(option) is not int or not 0<=option<len(options):raise BridgeError('PARAM_INVALID','请选择当前开场问题中的一个选项。')
        self._explain(course,state,ctx,options[option])

    def _explain(self, course, state, ctx, text):
        point=self._focus(course,state)[0]
        teaching=self._teacher(course,state,ctx,'顺着学生的开场回答讲本点重点。对的直接顺着讲，不重复铺垫；错了或不知道，不纠缠，一起看原文。每段简洁，不反复问清楚吗；不出计分题。',[point],user=text)
        self._deliver(course,state,teaching,user=text,explained=True)
        state['step']='teaching';state['conversation']['options']=[]

    def confused(self, course, state, ctx):
        if state['step'] not in ('opening','teaching','closing'):raise BridgeError('STEP_INVALID','可以先提问说明哪里不明白。')
        text='没太懂'
        teaching=self._teacher(course,state,ctx,'学生没太懂，依据同一原文换一种口语说法或生活化例子；不能引入新知识结论，不出计分题。',self._focus(course,state),user=text)
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
            director+='软件操作事实：客观题在选项或输入框作答、程序判；问答题用上方卡片先写自己的回答，再点看答案，逐条勾自己答到的要点并提交，全部勾到才算对。你只回答提问，不代替卡片、不改变软件的判法。'
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
            pending.update(learner_answer=answer,revealed=True)
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
        if ctx:ctx.stream(MATERIAL_EVENT+json.dumps(dict(text='正在找题…',done=0,total=1),ensure_ascii=False))
        try:
            self._materials().prepare_point(course.store,course.plan_id,course.point(point_id),dict(
                gateway=self.services.gateway,cancel=cancel,exclude_ids=exclude_ids,on_status=lambda text: ctx.stream(MATERIAL_EVENT+json.dumps(dict(text=text,done=0,total=1),ensure_ascii=False)) if ctx else None))
        except Exception as exc:
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
        if kind=='module_test' and state['step']!='test_offer':raise BridgeError('STEP_INVALID','模块还没讲完。')
        if course.module(mid).get('material_preparation',{}).get('state') is not None and not self._ready(course,mid):
            return self._wait(course,state,'start_test',(kind,))
        queue=self._questions(course,mid)
        if kind=='retest':
            if state['step']!='test_done' or book.module(mid).state!='failed':raise BridgeError('STEP_INVALID','现在没有待重测的题。')
            queue=[q for q in queue if book.question(q['id']).module_correct is False]
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
        state.update(step='retesting' if kind=='retest' else 'testing',
            test=dict(kind=kind,attempt=uuid.uuid4().hex[:16],queue=queue,index=0,targets=[q['id'] for q in queue],resume=resume))
        self._ask_next(course,state,ctx)

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
        review['question']=_link_citations(teaching.speech,{f'c{i}':cid for i,cid in enumerate(sorted(course.excerpts([pid])),1)})

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
        existing={r['id'] for r in course.store.records('credits',course.plan_id)}
        for point in course.points_of(state['module_id']):
            credited=sum(c['minutes'] for c in live if c['unit_id']==point['id'])
            course.store.confirm_credits(course.plan_id,point['id'],event_id=f"confirm-direct:{attempt}:{point['id']}",today=self._day(state))
            missing=max(0,point['minutes']-credited)
            if missing:
                credit_id=f"learn:{point['id']}" if f"learn:{point['id']}" not in existing else f"relearn:direct:{attempt}:{point['id']}"
                course.store.add_credit(course.plan_id,credit_id,point['id'],missing,self._day(state),'confirmed')

    def _ask_next(self, course, state, ctx=None):
        test=state['test']
        if test['index']<len(test['queue']):
            self._show_question(course,state,test['queue'][test['index']],test['kind'],number=test['index']+1,total=len(test['queue']))
        else:self._finish_test(course,state,ctx)

    def start_check(self, course, state, ctx):
        if state['step'] not in ('check_offer','checking') or state.get('pending'):raise BridgeError('STEP_INVALID','请先完成当前步骤。')
        pending = next((m['id'] for m in course.modules if not self._ready(course,m['id'])),None)
        if pending is not None:
            job=self._prepare(course,pending)
            state['material_wait']=dict(module_id=pending,action='start_check',args=[],previous_step=state['step'],
                done=job['done'],total=job['total'],text=job['text'],error=job['error'])
            state['step']='preparing_materials'
            return
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
        if not ids:raise BridgeError('CHECK_PENDING','请先学完或检测通过所有模块。')
        bank={q['id']:q for q in self._questions(course)}
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

    def _teacher(self, course, state, ctx, director, point_ids, *, user='',opening=False,allow_plan=False):
        plan,module=self._skeleton(course,state,point_ids)
        excerpts=self._teaching_excerpts(course,state,point_ids)
        available=self._related_passages(course,excerpts,user) if allow_plan else excerpts
        document=(course.store.records('teacher_profile',course.plan_id) or [{}])[0].get('document',{})
        tctx=dict(plan=plan,module=module,prefs=self._prefs(course),profile=document.get('text','')[:6000],dialogue=self._dialogue(course,state),
            current_points=[dict(id=pid,title=course.point(pid)['title']) for pid in point_ids],
            director=director,user=user,excerpts=excerpts,plan_passages=available,opening=opening)
        tctx['request_page']=self._teacher_request_page
        concept=self._concept(course,state)
        if concept:
            point,sub,index=concept
            wrapping=(state.get('concept') or {}).get('wrapping',False)
            tctx['concept']=dict(title=point['title'],goal=point.get('goal',point['summary']),
                subpoint_id=sub['id'],title_current=sub['title'],number=index+1,total=len(point['subpoints']),
                completed_titles=[s['title'] for s in point['subpoints'][:index]],wrapping=wrapping,
                notes=[n for s in (point['subpoints'] if wrapping else [sub]) for n in s.get('notes',[])])
        request=teacher_agent.teacher_request(tctx)
        for attempt in range(2):
            if ctx and ctx.cancel.is_set():raise ModelCancelled()
            # Validate the full reply before displaying it, including removing
            # invalid source labels. Uncited Chinese explanations are valid.
            result=self.services.gateway.complete('teacher',request[1],system=request[0],on_text=lambda delta:None,
                cancel=ctx.cancel if ctx else None,plan_id=course.plan_id,max_tokens=4096)
            try:
                teaching=teacher_agent.parse_teaching(result.text,tctx)
                self._teacher_request_page.accept(request[0],request[1],teacher_agent._dialogue(tctx['dialogue']),teacher_agent.history_text(result.text))
                if ctx:
                    ctx.stream(teaching.speech)
                return teaching
            except AgentFormatError as exc:
                if ctx:ctx.stream(RESET)
                if attempt==0:
                    request=(request[0],request[1]+[dict(role='user',content=f'上一条回复格式不合格：{exc}。用中文解释本步内容，不出计分题、不声称修改计划；开场才给选项。重新写一次。')])
                else:raise

    @staticmethod
    def _related_passages(course, excerpts, user):
        """Retrieve bounded source text for an interruption, not the whole book."""
        words=set(re.findall(r'[a-zA-Z][a-zA-Z0-9_-]{1,}',user.lower()))
        for part in re.findall(r'[\u3400-\u9fff]+',user):
            words.update(part[i:i+n] for n in (2,3,4) for i in range(len(part)-n+1))
        ranked=[]
        for cid,row in course.passages([p['id'] for p in course.points]).items():
            if cid in excerpts:continue
            text=row['text'];lower=text.lower()
            score=sum(len(word) for word in words if word in lower)
            if score:ranked.append((-score,cid,text))
        result=dict(excerpts);remaining=16000
        for _,cid,text in sorted(ranked):
            if remaining<=0 or len(result)-len(excerpts)>=12:break
            result[cid]=text[:min(4000,remaining)];remaining-=len(result[cid])
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
        if message_id and any(m['id']==message_id for m in course.store.messages(course.plan_id,self._channel(state))):return message_id
        previous=course.store.messages(course.plan_id,self._channel(state))
        at=self.now()
        if previous:at=max(at,datetime.fromisoformat(previous[-1]['at'])+timedelta(microseconds=1))
        return course.store.append_message(course.plan_id,self._channel(state),role,text,kind=kind,message_id=message_id,at=at)

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
        course.store.add_credit(course.plan_id,f"learn:{point['id']}",point['id'],point['minutes'],self._day(state),'tentative')

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


    def next_module(self, course, state, ctx):
        if state["step"] not in ("test_done",):
            raise BridgeError("STEP_INVALID", "请先完成这个模块的检测。")
        book = course.book()
        after = [m for m in course.modules if book.module(m["id"]).state == "untested"
                 and m['id'] not in state.get('skipped_modules', [])]
        if after:
            state.update(module_id=after[0]["id"], index=0, step="teach", pending=None, test=None, stems=[])
            return
        failed = [m for m in course.modules if book.module(m["id"]).state == "failed"]
        if failed:
            state.update(module_id=failed[0]["id"], index=len(course.points_of(failed[0]["id"])), step="test_done",
                         pending=None, test=None)
            self._say(course, state, "所有模块都学过了。这个模块还有没过的题，重测答错的题后才能进入最终检验。", role="system")
            return
        if not book.check_ready():
            self._say(course, state, '本次可学模块已结束。出题失败的点未算通过，稍后重试检测；可以继续学习其他模块。', role='system')
            return
        state.update(step="check_offer", pending=None, test=None)
        if not self._questions(course) and all(self._ready(course,m['id']) for m in course.modules):return self._complete(course,state,ctx)
        self._say(course,state,'全部模块都学完或通过了。最终检验会考全部题；答错过的题需连续答对两遍。',role='system')


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


    def _dialogue(self, course, state):
        rows = []
        for m in course.store.messages(course.plan_id, self._channel(state)):
            role = "user" if m["role"] == "user" else "assistant"
            text = m["content"] if m["role"] != "system" else f"（系统）{m['content']}"
            rows.append({"role": role, "text": text})
        return dialogue_tail(rows, max_rows=DIALOGUE_ROWS, max_chars=DIALOGUE_CHARS)


    def _focus(self, course, state):
        if state.get('pending'):return state['pending']['point_ids']
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
            store.append_attempt_closed(course.plan_id,AttemptClosed(uuid.uuid4().hex,course.next_seq(),mid,test['attempt'],test['kind']))
        store.settle_module_test(course.plan_id,mid,test['attempt'],today=day)
        book=course.book();passed=book.module(mid).state in ('passed','skipped')
        if passed and book.module(mid).state=='skipped':
            self._direct_credits(course,state,test['attempt'])
        elif passed or test['kind']=='module_test':
            for p in course.points_of(mid):
                store.confirm_credits(course.plan_id,p['id'],event_id=f"confirm:{test['attempt']}:{p['id']}",today=day)
        correct=sum(book.question(q['id']).module_correct is True for q in self._questions(course,mid));total=len(self._questions(course,mid))
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
        return {c["credit"]["unit_id"] for c in self.store.records("credits", self.plan_id)
                if c["id"].startswith(("learn:", "relearn:"))}

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
        room().cancel_materials(p.get('plan_id'))
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
        return dict(result,reference=q.get('reference'),key_points=q.get('key_points'))
    def submit(p,c):
        result=invoke('recall_submit',p,c,p.get('question_id'),p.get('ticked'),p.get('correct'))
        if result.get('failure'):return result
        return dict(result,**(result.get('recall_result') or {}))
    methods={
        'class.recall_reveal':reveal,'class.recall_submit':submit,
        'class.recall_draft':lambda p,c:invoke('recall_draft',p,c,p.get('question_id'),p.get('ticked')),
        'classroom.wrong_answers':lambda p,c:room().services.store.wrong_answers(p.get('plan_id')),
        'classroom.enter':enter,'classroom.leave':leave,'classroom.source':source,
        'classroom.state':lambda p,c:room().run(p.get('plan_id'),lambda *args:None,c),
        'classroom.next':lambda p,c:invoke('next',p,c),
        'classroom.choose':lambda p,c:invoke('choose',p,c,p.get('option')),
        'classroom.confused':lambda p,c:invoke('confused',p,c),
        'classroom.ask':lambda p,c:invoke('ask',p,c,text(p)),
        'classroom.reply':lambda p,c:invoke('reply',p,c,text(p)),
        'classroom.answer':lambda p,c:invoke('answer',p,c,p.get('value')),
        'classroom.why':lambda p,c:invoke('why',p,c),
        'classroom.back':lambda p,c:invoke('back',p,c),
        'classroom.retry':lambda p,c:invoke('retry',p,c),
        'classroom.cancel_preparation':lambda p,c:invoke('stop_preparation',p,c),
        'classroom.gaps':lambda p,c:room()._file(p.get('plan_id')).read({}).get('question_gaps',[]),
        'classroom.test':test,
        'classroom.direct_next':lambda p,c:invoke('direct_next',p,c),
        'classroom.direct_choice':lambda p,c:invoke('direct_choice',p,c,p.get('skip')),
        'classroom.next_module':lambda p,c:invoke('next_module',p,c),
        'classroom.check':lambda p,c:invoke('start_check',p,c),
        'classroom.practice':lambda p,c:invoke('start_practice',p,c,p.get('module_id'),p.get('point_id'),p.get('count',5)),
    }
    for name,fn in methods.items():bridge.register(name,fn)
