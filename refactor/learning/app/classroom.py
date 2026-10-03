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
import unicodedata
import uuid
from types import SimpleNamespace

from ..connections.errors import ModelCancelled, ModelError
from ..course import judge_agent, teacher_agent, question_bank
from ..course.request_layout import AppendOnlyPage, dialogue_tail
from ..course.mastery import ANGLES, AttemptClosed, Judgement
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
PURPOSE = {"casual": "quick", "quick": "quick", "module_test": "module", "skip_test": "direct",
           "retest": "retest", "check": "final", "practice": "free"}


def _now():
    return datetime.now(timezone.utc)


def _normal(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


class Classroom:
    _mutex = threading.Lock()
    _room_locks = {}
    _inflight = {}

    def __init__(self, services, study_day, *, now=_now, rng=None, review_wrong=None):
        self.services, self.study_day, self.now = services, study_day, now
        self._rng = rng or random.Random()
        self._jobs, self._entered_plans = {}, set()
        self._local = threading.local()
        self.material_event = lambda data: None
        self.review_wrong = judge_agent.REVIEW_WRONG_DEFAULT if review_wrong is None else review_wrong
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
            message = "正在判定这道题，请稍后再问。" if self._inflight.get(self._activity_key(plan_id)) == "judging" else "老师正在回复，请稍后再发；已有内容和位置保留。"
            raise BridgeError("CLASS_BUSY", message)
        try:
            course = self._course(plan_id)
            state = self._load(course)
            action = getattr(self._local, "action", None)
            if action and action[2] in state.get("completed_requests", []):
                return self.snapshot(course, state)
            original = copy.deepcopy(state)
            try:
                if self._test_already_closed(course, state):
                    self._finish_test(course, state, ctx)
                else:
                    work(course, state, ctx)
                    if not action or action[0]!='leave':self._resume_materials(course, state, ctx)
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
            self._inflight.pop(self._activity_key(plan_id), None)
            lock.release()

    def _load(self, course):
        try:
            state = self._file(course.plan_id).read({})
        except StorageError as exc:
            raise BridgeError("CLASS_READ_FAILED", "课堂位置读取失败，请重试；不会覆盖已有文件。") from exc
        modules = {m['id'] for m in course.modules}
        if state.get('version') != 2 or state.get('module_id') not in modules:
            state = self._rebuild(course)
        room = (state.get('practice') or {}).get('resume', state)
        if (room['step'] == 'completed') != (course.plan['state'] == 'completed'):
            state = self._rebuild(course)
        return state

    def _save(self, course, state):
        try:
            self._file(course.plan_id).write(state)
        except StorageError as exc:
            raise BridgeError('CLASS_SAVE_FAILED', '课堂位置保存失败，请重试；已入库判定会按编号恢复。') from exc

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
        for m in course.store.messages(course.plan_id,self._channel(state)):
            cards=[]
            for cid in dict.fromkeys(re.findall(r'mf-source:([^\s)]+)',m['content'])):
                if cid in course.passages([p['id'] for p in course.points]):cards.append(course.passage_card(cid))
            messages.append(dict(id=m['id'],role=m['role'],kind=m.get('kind','text'),content=m['content'],sources=cards))
        question = {k:q.get(k, []) if k=='options' else q[k] for k in ('id','kind','stem','options','source','origin')} if q else None
        if question and q['kind']=='fill':question['blanks']=len(q['answer'])
        if question:question.update(purpose=PURPOSE[pending['kind']],number=pending.get('number'),total=pending.get('total'))
        today = self._today(course,state)
        return dict(plan={k:course.plan.get(k) for k in ('id','goal','state')},
            module=dict(id=module['id'],title=module['title'],number=course.modules.index(module)+1,total=len(course.modules),state=book.module(module['id']).state),
            points=[dict(id=p['id'],title=p['title'],minutes=p['minutes'],taught=p['id'] in taught,state='weak' if book.point(p['id']).casual_errors and book.point(p['id']).state=='untested' else book.point(p['id']).state,current=i==state['index']) for i,p in enumerate(points)],
            step=state['step'],question=question,messages=messages,conversation=state.get('conversation'),
            preparation=state.get('material_wait') or self._final_progress(course),failure=state.get('failure'),explanation=state.get('answer_explanation'),
            can_skip=state['step']=='teach' and state['index']==0 and not any(p['id'] in taught for p in points),
            red=[p['title'] for p in points if book.point(p['id']).state=='red'],
            more_modules=any(book.module(m['id']).state=='untested' for m in course.modules if m['id']!=module['id']),
            check=dict(passed=sum(book.point(p['id']).level==3 for p in course.points),total=len(course.points)) if state.get('phase')=='check' or state['step'] in ('check_offer','completed') else None,today=today)

    @staticmethod
    def _materials():
        try:return importlib.import_module('refactor.learning.course.point_materials')
        except ImportError as exc:raise BridgeError('MATERIAL_COMPONENT_MISSING','资料与题库组件尚未接入，请稍后重试。') from exc

    def _ready(self, course, module_id):
        bank=course.store.records('question_bank',course.plan_id)
        return all(course.passages([p['id']]) and sum(p['id'] in q.get('point_ids',[]) and q.get('role','bank')=='bank' for q in bank)>=2
                   and any(q.get('role')=='quick' and q.get('point_ids')==[p['id']] for q in bank) for p in course.points_of(module_id))

    def _fingerprint(self, course, module_id):
        data=[{k:v for k,v in p.items() if k!='chunk_ids'} for p in course.points_of(module_id)]
        return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

    def _prepare(self, course, module_id):
        key=(course.plan_id,module_id);fingerprint=self._fingerprint(course,module_id)
        job=self._jobs.get(key)
        if job and job['fingerprint']==fingerprint:return job
        if job:job['cancel'].set()
        job=dict(fingerprint=fingerprint,status='running',done=0,total=len(course.points_of(module_id)),text='正在准备原文和带答案的题目',error=None,cancel=threading.Event())
        self._jobs[key]=job
        def progress(done,total,text):
            if job['cancel'].is_set() or self._jobs.get(key) is not job:return
            job.update(done=done,total=total,text=text)
            self.material_event(dict(plan_id=course.plan_id,module_id=module_id))
        def worker():
            try:
                ctx=dict(cancel=job['cancel'],gateway=self.services.gateway,
                         on_status=lambda text: progress(job['done'],job['total'],text))
                self._materials().prepare_module(course.store,course.plan_id,module_id,ctx,progress)
                if not job['cancel'].is_set():
                    fresh=self._course(course.plan_id)
                    if self._fingerprint(fresh,module_id)!=fingerprint:
                        job['status']='stale'
                    else:job['status']='ready'  # Shortages are handled per point, not an endless module gate.
            except Exception as exc:
                log.info('material preparation failed',exc_info=True)
                job.update(status='failed',error=getattr(exc,'message','资料准备没有完成，请重试。'))
            finally:
                if not job['cancel'].is_set() and self._jobs.get(key) is job:
                    self.material_event(dict(plan_id=course.plan_id,module_id=module_id))
        threading.Thread(target=worker,name='classroom-materials',daemon=True).start()
        return job

    def _wait(self, course, state, action, args=()):
        mid=state['module_id'];job=self._prepare(course,mid)
        state['material_wait']=dict(module_id=mid,action=action,args=list(args),previous_step=state['step'],done=job['done'],total=job['total'],text=job['text'],error=job['error'])
        state['step']='preparing_materials'

    def _resume_materials(self, course, state, ctx):
        wait=state.get('material_wait')
        if not wait:return
        job=self._jobs.get((course.plan_id,wait['module_id'])) or self._prepare(course,wait['module_id'])
        wait.update(done=job['done'],total=job['total'],text=job['text'],error=job['error'])
        if job['status']=='ready':
            state.setdefault('materials_attempted',{})[wait['module_id']]=job['fingerprint']
            state['step']=wait['previous_step'];state.pop('material_wait')
            getattr(self,wait['action'])(course,state,ctx,*wait['args'])

    def enter(self, course, state, ctx):
        if course.plan_id not in self._entered_plans or not state.get('in_session'):
            state['day']=self.study_day().isoformat()
        self._entered_plans.add(course.plan_id);state['in_session']=True
        for m in course.modules:
            if not self._ready(course,m['id']):self._prepare(course,m['id'])

    def leave(self, course, state, ctx):
        self._entered_plans.discard(course.plan_id)
        self.cancel_materials(course.plan_id)
        state['in_session']=False;state['day']=None
        self._profile(course)

    def retry(self, course, state, ctx):
        failure=state.pop('failure',None)
        if failure:
            self._local.action=(failure['action'],failure['args'],failure['request_id'])
            return getattr(self,failure['action'])(course,state,ctx,*failure['args'])
        wait=state.get('material_wait')
        if wait:
            job=self._jobs.pop((course.plan_id,wait['module_id']),None)
            if job:job['cancel'].set()
            self._prepare(course,wait['module_id'])

    def stop_preparation(self, course, state, ctx):
        self.cancel_materials(course.plan_id)
        wait=state.pop('material_wait',None)
        if wait:state['step']=wait['previous_step']

    def next(self, course, state, ctx):
        if state['step']=='preparing_materials':return
        if state['step']=='closing':
            self._point_done(course,state)
            if state['step']!='teach':return
            if self._target_met(course,state):return
        if state['step']=='teaching':return self._casual_card(course,state,ctx)
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
        chapter=f"先用一句话说明这一章要解决什么问题：{course.module(state['module_id'])['title']}。" if state['index']==0 else ''
        teaching=self._teacher(course,state,ctx,chapter+'用一个生活化问题开头，给2～3个不计分的可点选项，也允许打字。先不要讲完整结论。',[point['id']],opening=True)
        self._deliver(course,state,teaching)
        state.update(step='opening',conversation=dict(point_id=point['id'],options=list(teaching.options),interrupted=False))

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
        # A pending answer is not an active grading request. The explicit ask
        # action never goes through the grader and never changes the position.
        point_ids=self._focus(course,state)
        teaching=self._teacher(course,state,ctx,'正常回答学生追问，依据本点和本计划已采用资料；没有依据就直说“资料里没讲到”，可在计划里补充资料。回答完回到原来的位置，不出题、不判分。',point_ids,user=text,allow_plan=True)
        self._deliver(course,state,teaching,user=text)
        if state.get('conversation'):state['conversation']['interrupted']=True

    def back(self, course, state, ctx):
        if state.get('conversation'):state['conversation']['interrupted']=False

    def reply(self, course, state, ctx, text):
        if state['step']=='opening':return self._explain(course,state,ctx,text)
        pending=state.get('pending')
        if not pending:
            if text.strip() in ('我会了','我已经会了') and state['step'] in ('teaching','closing'):
                return self._casual_card(course,state,ctx,quick=True)
            return self.ask(course,state,ctx,text)
        verdict=self._prior_verdict(course,state,pending) or self._judge(course,state,ctx,pending,text)
        if verdict.kind=='question':return self.ask(course,state,ctx,text)
        if verdict.kind=='claim_known' and pending['kind']=='casual':return self._casual_card(course,state,ctx,quick=True)
        if verdict.kind in ('insufficient','process_missing'):
            prompt='写一下你是怎么得到的。' if verdict.kind=='process_missing' else '能再具体说说你的回答吗？'
            if pending.get('followup'):
                raise BridgeError('JUDGE_UNCLEAR','补充后仍无法完成判定，请重试；本题还没有记分。')
            pending['followup']=text
            self._user(course,state,text);self._say(course,state,prompt,kind='question')
            return
        if verdict.kind=='clarify' or verdict.uncertain:
            replacement=self._pick(course,pending['point_ids'][0],PURPOSE[pending['kind']],exclude=(pending['question']['id'],),state=state,ctx=ctx)
            if replacement is None:
                state['pending']=None
                return self._advance_missing(course,state,ctx,pending['kind'])
            self._user(course,state,text)
            self._say(course,state,'这次判定拿不准，同一点换一道题确认；刚才的作答还没有记分。')
            old=pending['kind'];state['pending']=None
            return self._show_question(course,state,replacement,old,asks=pending.get('asks',1))
        if verdict.kind!='answer':
            self._user(course,state,text)
            self._say(course,state,'对错和进度由系统记录，老师不能直接判过；你可以提问，也可以回答当前题。')
            return
        return self._settle(course,state,ctx,pending,verdict,text)

    def answer(self, course, state, ctx, value):
        pending=state.get('pending')
        if not pending:raise BridgeError('STEP_INVALID','现在没有待答题。')
        q=pending['question'];kind=q['kind']
        if kind in ('choice','multi'):
            if not isinstance(value,list) or any(type(i) is not int or not 0<=i<len(q['options']) for i in value) or len(set(value))!=len(value) or not value or (kind=='choice' and len(value)!=1):
                raise BridgeError('PARAM_INVALID','请选择有效的选项。')
            good=set(value)==set(q['answer'])
            verdict=judge_agent.Verdict('answer',good,() if good else tuple(q['point_ids']),'选项与资料答案一致。' if good else '选项与资料答案不一致。')
            return self._settle(course,state,ctx,pending,self._prior_verdict(course,state,pending) or verdict,'、'.join(q['options'][i] for i in sorted(value)))
        if kind=='fill':
            if not isinstance(value,list) or len(value)!=len(q['answer']) or any(not isinstance(x,str) or len(x)>4000 for x in value):raise BridgeError('PARAM_INVALID','请填写每一个空。')
            if all(any(_normal(v)==_normal(a) for a in allowed) for v,allowed in zip(value,q['answer'])):
                verdict=judge_agent.Verdict('answer',True,(),'填空符合资料列出的可接受写法。')
                return self._settle(course,state,ctx,pending,self._prior_verdict(course,state,pending) or verdict,'；'.join(value))
            return self.reply(course,state,ctx,'；'.join(value))
        raise BridgeError('PARAM_INVALID','这道题请用文字作答。')

    def _judge(self, course, state, ctx, pending, text):
        q=pending['question']
        combined=f"原回答：{pending['followup']}\n补充回答：{text}" if pending.get('followup') else text
        jctx=dict(mode='casual',points=[course.point(pid) for pid in pending['point_ids']],
            excerpts=course.excerpts(pending['point_ids']),question=q,reference=self._reference(q),
            information_followed_up=bool(pending.get('followup')),require_evidence=q['kind'] in ('short','essay','calc'))
        request=judge_agent.judge_request(jctx,combined)
        self._inflight[self._activity_key(course.plan_id)]='judging'
        try:
            result=self.services.gateway.complete('judge',request[1],system=request[0],cancel=ctx.cancel if ctx else None,plan_id=course.plan_id,max_tokens=4096)
            first=judge_agent.defer_incomplete(judge_agent.parse_judgement(result.text,jctx),jctx)
            if self.review_wrong and jctx['require_evidence'] and first.kind=='answer' and first.correct is False and not first.uncertain:
                second=self.services.gateway.complete('judge',request[1],system=request[0],cancel=ctx.cancel if ctx else None,plan_id=course.plan_id,max_tokens=4096)
                return judge_agent.review_verdict(first,judge_agent.parse_judgement(second.text,jctx))
            return first
        finally:self._inflight[self._activity_key(course.plan_id)]='working'

    @staticmethod
    def _reference(q):
        if q['kind'] in ('choice','multi'):return '\n'.join(q['options'][i] for i in q['answer'])
        if q['kind']=='fill':return '\n'.join(' / '.join(a) for a in q['answer'])
        return q['answer']

    def _pick(self, course, point_id, purpose, exclude=(), *, state=None, ctx=None):
        history=self._test_questions(course,point_id) if purpose in ('retest','final') else []
        avoid=tuple(dict.fromkeys([*exclude,*history]))
        def select():
            q=self._materials().pick_question(course.store,course.plan_id,point_id,purpose,exclude_ids=avoid)
            role='quick' if purpose=='quick' else 'bank'
            return q if q and q.get('role','bank')==role else None
        q=select()
        if q is None and history:
            # Prefer another question, but a shortage is allowed to reuse the original test.
            bank={q['id']:q for q in course.store.records('question_bank',course.plan_id)}
            fallback=self._test_questions(course,point_id,include_retest=False) if purpose=='final' else history
            q=next((bank[qid] for qid in reversed(fallback) if qid in bank and qid not in exclude and bank[qid].get('role','bank')=='bank'),None)
        if q is None:
            reason=self._prepare_one(course,point_id,ctx)
            q=select()
            if q is None:
                if state is not None:self._skip_question(course,state,point_id,purpose,reason or '准备后仍没有带答案的题目')
                return None
        q=copy.deepcopy(q)
        if q.get('kind') not in ('choice','multi','fill','short','essay','calc') or not q.get('answer'):
            raise BridgeError('QUESTION_INVALID','题库里这道题缺少有效参考答案，请重新准备。')
        if not isinstance(q.get('point_ids'),list) or point_id not in q['point_ids'] or any(pid not in {p['id'] for p in course.points_of(course.point(point_id)['module_id'])} for pid in q['point_ids']):raise BridgeError('QUESTION_INVALID','题目与当前模块知识点不对应。')
        if not isinstance(q.get('stem'),str) or not q['stem'].strip() or not q.get('id') or any(not isinstance(q.get(k),dict) or not all(isinstance(q[k].get(f),str) for f in ('title','locator')) or (q[k].get('url') is not None and not isinstance(q[k]['url'],str)) for k in ('source','answer_source')):
            raise BridgeError('QUESTION_INVALID','题库题面或原文出处不完整，请重新准备。')
        if q['kind'] in ('choice','multi'):
            if not isinstance(q.get('options'),list) or len(q['options'])<2 or any(not isinstance(o,str) or not o.strip() for o in q['options']) or not isinstance(q['answer'],list) or any(type(i) is not int or not 0<=i<len(q['options']) for i in q['answer']) or len(set(q['answer']))!=len(q['answer']) or (q['kind']=='choice' and len(q['answer'])!=1):
                raise BridgeError('QUESTION_INVALID','题库选项或答案下标无效，请重新准备。')
        elif q['kind']=='fill':
            if not isinstance(q['answer'],list) or any(not isinstance(a,list) or not a or any(not isinstance(v,str) or not v.strip() for v in a) for a in q['answer']):
                raise BridgeError('QUESTION_INVALID','填空题可接受写法表不完整。')
        elif not isinstance(q['answer'],str):raise BridgeError('QUESTION_INVALID','题库参考答案须为原文。')
        if purpose=='quick' and (q['kind']!='choice' or q['point_ids']!=[point_id] or q.get('origin')!='cloze'):
            raise BridgeError('QUESTION_INVALID','随口问必须是当前点的原文挖空选择题。')
        return q

    @staticmethod
    def _test_questions(course, point_id, *, include_retest=True):
        return list(dict.fromkeys(r['question_id'] for r in course.store.records('question_answers',course.plan_id)
            if point_id in r['point_ids'] and r['context'] in (('module_test','skip_test','retest') if include_retest else ('module_test','skip_test'))))

    def _prepare_one(self, course, point_id, ctx):
        cancel=ctx.cancel if ctx else threading.Event()
        if cancel.is_set():raise ModelCancelled()
        if ctx:ctx.stream(MATERIAL_EVENT+json.dumps(dict(text='正在找题…',done=0,total=1),ensure_ascii=False))
        try:
            self._materials().prepare_point(course.store,course.plan_id,course.point(point_id),dict(
                gateway=self.services.gateway,cancel=cancel,on_status=lambda text: ctx.stream(MATERIAL_EVENT+json.dumps(dict(text=text,done=0,total=1),ensure_ascii=False)) if ctx else None))
        except Exception as exc:
            if cancel.is_set() or isinstance(exc,ModelCancelled):raise ModelCancelled() from None
            log.info('point preparation did not produce a question',exc_info=True)
            return '找题没有完成；本次已跳过，可补充资料后再试'
        if cancel.is_set():raise ModelCancelled()
        return '没有关联的原文段落' if not self._course(course.plan_id).passages([point_id]) else None

    def _skip_question(self, course, state, point_id, purpose, reason):
        entry=dict(point_id=point_id,title=course.point(point_id)['title'],purpose=purpose,reason=reason,day=self._day(state))
        gaps=state.setdefault('question_gaps',[])
        if not any(g['point_id']==point_id and g['purpose']==purpose and g['reason']==reason for g in gaps):gaps.append(entry)
        if purpose=='final' and point_id not in state.setdefault('check_missing',[]):state['check_missing'].append(point_id)
        self._say(course,state,f"{entry['title']}：资料缺失，已跳过本次检测（{reason}）。",kind='material_gap')

    def _advance_missing(self, course, state, ctx, kind):
        if kind in ('casual','quick'):state['step']='closing'
        elif kind in ('module_test','skip_test','retest'):
            state['test']['index']+=1
            if state['test']['index']<len(state['test']['queue']):self._ask_next(course,state)
            else:self._finish_test(course,state,ctx)
        elif kind=='check':
            state['check']['index']+=1
            self._next_check(course,state,ctx)
        else:
            state['practice']['index']+=1
            self._ask_practice(course,state,ctx)

    def _final_progress(self, course):
        job=self._jobs.get((course.plan_id,'final'))
        return {k:job[k] for k in ('done','total','text','error')} if job and job['status']=='running' else None

    def _prepare_final(self, course):
        book=course.book();bank=course.store.records('question_bank',course.plan_id)
        points=[p for p in course.points if book.point(p['id']).level<3 and (book.point(p['id']).module_errors or
                sum(p['id'] in q.get('point_ids',[]) and q.get('role','bank')=='bank' for q in bank)<3)]
        fingerprint=repr([(p['id'],book.point(p['id']).check_questions) for p in points])
        key=(course.plan_id,'final');previous=self._jobs.get(key)
        if not points or previous and previous['fingerprint']==fingerprint:return
        if previous:previous['cancel'].set()
        job=dict(fingerprint=fingerprint,status='running',done=0,total=len(points),text='正在为最终检验准备题目',error=None,cancel=threading.Event())
        self._jobs[key]=job
        def worker():
            try:
                for i,p in enumerate(points):
                    if job['cancel'].is_set():return
                    try:self._materials().prepare_point(course.store,course.plan_id,p,dict(gateway=self.services.gateway,cancel=job['cancel']))
                    except Exception:
                        if job['cancel'].is_set():return
                        log.info('final preparation shortage; retaining existing questions',exc_info=True)
                    job['done']=i+1;self.material_event(dict(plan_id=course.plan_id))
            finally:
                job['status']='ready'
                if not job['cancel'].is_set():self.material_event(dict(plan_id=course.plan_id))
        threading.Thread(target=worker,name='classroom-materials',daemon=True).start()

    def _show_question(self, course, state, q, kind, *, asks=1, number=None,total=None):
        self._materials().mark_used(course.store,course.plan_id,q['id'],PURPOSE[kind])
        state['pending']=dict(kind=kind,answer_id=uuid.uuid4().hex,point_ids=q['point_ids'],question=q,asks=asks,number=number,total=total)
        if kind in ('casual','quick'):state['step']='answer'
        # Retain the previous verdict's explanation while showing the next card.
        self._say(course,state,q['stem'],kind='question')

    def _casual_card(self, course, state, ctx, quick=False):
        pid=self._focus(course,state)[0]
        q=self._pick(course,pid,'quick',state=state,ctx=ctx)
        if q is None:
            state['step']='closing'
            return
        self._show_question(course,state,q,'quick' if quick else 'casual')

    def _explanation(self, course, q):
        source=q['answer_source'];text=q.get('explanation') or self._reference(q)
        return dict(text=text,source=source,label='资料解析' if q.get('explanation') else '参考答案原文')

    def why(self, course, state, ctx):
        if not state.get('answer_explanation'):raise BridgeError('STEP_INVALID','先回答一题，就可以看解析或答案出处。')
        state['answer_explanation']['open']=True

    def _settle(self, course, state, ctx, pending, verdict, text):
        kind=pending['kind'];q=pending['question'];context={'casual':'casual','quick':'quick_confirm','check':'check','practice':'practice'}.get(kind,kind)
        split=kind in ('module_test','skip_test','retest') and not verdict.correct and len(q['point_ids'])>1
        pieces=[]
        if split:
            for pid in q['point_ids']:
                piece=self._pick(course,pid,PURPOSE[kind],exclude=(q['id'],),state=state,ctx=ctx)
                if piece and piece['point_ids']==[pid]:pieces.append(piece)
                elif piece:self._skip_question(course,state,pid,PURPOSE[kind],'没有逐点确认题')
        with course.store.transaction():
            self._user(course,state,text)
            if kind=='practice':
                if not any(r['id']==pending['answer_id'] for r in course.store.records('practice_results',course.plan_id)):
                    course.store.append_practice_result(course.plan_id,q['id'],course.point(q['point_ids'][0])['module_id'],q['point_ids'],verdict.correct,reason=verdict.reason,at=self.now(),result_id=pending['answer_id'],wrong_points=list(verdict.wrong_points))
            elif not split:
                combined=f"原回答：{pending['followup']}\n补充回答：{text}" if pending.get('followup') else text
                self._record(course,state,context,pending,verdict,combined)
            self._answer_fact(course,state,context,pending,verdict)
            verdict_id=self._say(course,state,'✓ 答对了。' if verdict.correct else '✗ 这题没答对。',kind='verdict')
        explanation=self._explanation(course,q)
        if kind in ('casual','quick'):
            paragraphs=course.passages(pending['point_ids'])
            original=q.get('explanation') or next(iter(paragraphs.values()),{}).get('text') or self._reference(q)
            restored=q['stem'].replace('____',self._reference(q),1)
            if '____' in q['stem'] and any(restored in p['text'] for p in paragraphs.values()):original=restored
            if not q.get('explanation'):
                original=next((s for s in re.split(r'(?<=[。！？])|\n',original) if self._reference(q) in s),original)
            explanation=dict(text=original,source=q['answer_source'],label='刚讲的原文')
        state['answer_explanation']=dict(explanation,open=not verdict.correct,message_id=verdict_id)
        if not verdict.correct:self._say(course,state,state['answer_explanation']['text'],kind='answer_source',role='assistant')
        if ctx:ctx.stream(VERDICT_EVENT+json.dumps(dict(text='答对了' if verdict.correct else '这题没答对',correct=verdict.correct),ensure_ascii=False))
        state['pending']=None
        if kind in ('casual','quick'):
            if kind=='quick' and verdict.correct:
                self._point_done(course,state);return
            state['step']='closing'
        elif kind in ('module_test','skip_test','retest'):
            test=state['test'];test['index']+=1
            if split:
                test['queue'][test['index']:test['index']]=pieces
                self._say(course,state,'综合题未通过，暂不归因到任何点；用题库里的逐点题分别确认。')
            if test['index']<len(test['queue']):self._ask_next(course,state)
            else:self._finish_test(course,state,ctx)
        elif kind=='check':
            state['check_used']=state.get('check_used',0)+1;state['check']['index']+=1
            if not verdict.correct:self._prepare_final(course)
            self._next_check(course,state,ctx)
        else:
            practice=state['practice'];practice['index']+=1
            if practice['index']>=practice['count']:
                resume=practice['resume'];explanation=state.get('answer_explanation');state.clear();state.update(resume);state['answer_explanation']=explanation
                self._say(course,state,'这一轮自由检验完成了，计划与学习报告不变。')
            else:self._ask_practice(course,state,ctx)

    def _point_done(self, course, state):
        point=course.points_of(state['module_id'])[state['index']]
        self._credit_learning(course,state,point)
        state['index']+=1;state['pending']=None;state.pop('conversation',None)
        state['step']='teach' if state['index']<len(course.points_of(state['module_id'])) else 'test_offer'

    def start_test(self, course, state, ctx, kind):
        book=course.book();mid=state['module_id'];points=course.points_of(mid)
        if kind=='skip_test' and (state['step']!='teach' or state['index']!=0 or any(p['id'] in course.taught() for p in points)):raise BridgeError('STEP_INVALID','只有模块开始前才能直接考。')
        if kind=='module_test' and state['step']!='test_offer':raise BridgeError('STEP_INVALID','模块还没讲完。')
        targets=[p['id'] for p in points if kind!='retest' or book.point(p['id']).state=='red']
        if kind=='retest' and (state['step']!='test_done' or not targets):raise BridgeError('STEP_INVALID','现在没有待重测的点。')
        queue=[];covered=set()
        for pid in targets:
            if pid not in covered:
                q=self._pick(course,pid,PURPOSE[kind],state=state,ctx=ctx)
                if q:queue.append(q);covered.update(q['point_ids'])
        state.update(step='retesting' if kind=='retest' else 'testing',test=dict(kind=kind,attempt=uuid.uuid4().hex[:16],queue=queue,index=0,targets=targets))
        if queue:self._ask_next(course,state)
        else:self._finish_test(course,state,ctx)

    def _ask_next(self, course, state):
        test=state['test'];self._show_question(course,state,test['queue'][test['index']],test['kind'],number=test['index']+1,total=len(test['queue']))

    def start_check(self, course, state, ctx):
        if state['step'] not in ('check_offer','checking') or state.get('pending'):raise BridgeError('STEP_INVALID','请先完成当前步骤。')
        self._prepare_final(course)
        if course.plan['state']=='active':
            course.store.start_checking(course.plan_id);course.plan=course.store.get_plan(course.plan_id)
        state.update(phase='check',step='checking',pending=None,check=None,check_used=0,check_missing=[])
        self._next_check(course,state,ctx)

    def _next_check(self, course, state, ctx):
        check=state.get('check')
        if check and check['index']<len(check['queue']):return self._ask_check(course,state)
        book=course.book()
        if book.check_complete():return self._complete(course,state,ctx)
        if state.get('check_used',0)>=CHECK_BATCH:
            state['check']=None;return self._say(course,state,'这组已答完，点继续检验开始下一组。')
        items=book.next_check_items(n=max(24,len(course.points)*4),min_gap=3)
        if not items:raise BridgeError('CHECK_PENDING','还有知识点需要确认，请继续检验。')
        selected=[];seen=set()
        for pid,_ in items:
            if pid in state.get('check_missing',[]):continue
            if pid not in seen:
                selected.append(pid);seen.add(pid)
            if len(selected)>=CHECK_BATCH-state.get('check_used',0):break
        queue=[]
        for pid in selected:
            q=self._pick(course,pid,'final',state=state,ctx=ctx)
            if q:queue.append(q)
        if not queue:
            state['check']=None
            self._say(course,state,'本组没有可用题，资料缺失的点已跳过；可以下课，到计划详情补充资料后继续检验。')
            return
        state['check']=dict(queue=queue,index=0)
        self._ask_check(course,state)

    def _ask_check(self, course, state):
        check=state['check'];q=check['queue'][check['index']]
        state['module_id']=course.point(q['point_ids'][0])['module_id']
        state['index']=next(i for i,p in enumerate(course.points_of(state['module_id'])) if p['id']==q['point_ids'][0])
        self._show_question(course,state,q,'check')

    def start_practice(self, course, state, ctx, module_id=None, point_id=None, count=5):
        if type(count) is not int or not 1<=count<=50:raise BridgeError('PARAM_INVALID','题数须为1～50。')
        if state.get('practice'):raise BridgeError('STEP_INVALID','当前自由检验还没结束。')
        if module_id and module_id not in {m['id'] for m in course.modules}:raise BridgeError('PARAM_INVALID','模块不存在。')
        if point_id and point_id not in {p['id'] for p in course.points}:raise BridgeError('PARAM_INVALID','知识点不存在。')
        targets=[p['id'] for p in course.points if (not module_id or p['module_id']==module_id) and (not point_id or p['id']==point_id) and (course.plan['state']=='completed' or p['id'] in course.taught())]
        if not targets:raise BridgeError('STEP_INVALID','所选范围还没有学过。')
        state['practice']=dict(resume=copy.deepcopy(state),targets=targets,index=0,count=count,asked=[])
        state.update(step='practicing',phase='practice',pending=None)
        self._ask_practice(course,state,ctx)

    def _ask_practice(self, course, state, ctx):
        practice=state['practice'];targets=list(practice['targets']);self._rng.shuffle(targets)
        review=question_bank.review_points(course.store.records('practice_results',course.plan_id))
        targets.sort(key=lambda pid:pid not in review)
        q=None
        for pid in targets:
            q=self._pick(course,pid,'free',state=state,ctx=ctx)
            if q:break
        if q is None:
            resume=practice['resume'];gaps=state.get('question_gaps',[]);state.clear();state.update(resume);state['question_gaps']=gaps
            return self._say(course,state,'本轮没有可用题，资料缺失的点已跳过；练习已结束，计划与报告不变。')
        state['module_id']=course.point(pid)['module_id']
        state['index']=next(i for i,p in enumerate(course.points_of(state['module_id'])) if p['id']==pid)
        self._show_question(course,state,q,'practice',number=practice['index']+1,total=practice['count'])

    def _teacher(self, course, state, ctx, director, point_ids, *, user='',opening=False,allow_plan=False):
        plan,module=self._skeleton(course,state,point_ids)
        excerpts=course.excerpts(point_ids)
        available=course.excerpts([p['id'] for p in course.points]) if allow_plan else excerpts
        document=(course.store.records('teacher_profile',course.plan_id) or [{}])[0].get('document',{})
        tctx=dict(plan=plan,module=module,prefs=self._prefs(),profile=document.get('text','')[:6000],dialogue=self._dialogue(course,state),
            current_points=[dict(id=pid,title=course.point(pid)['title']) for pid in point_ids],
            director=director,user=user,excerpts=excerpts,plan_passages=available,opening=opening)
        tctx['request_page']=self._teacher_request_page
        request=teacher_agent.teacher_request(tctx)
        for attempt in range(2):
            if ctx and ctx.cancel.is_set():raise ModelCancelled()
            # Paragraphs can stream only once their allowed citation appears.
            buffer=[];emitted=[0]
            def stream(delta):
                buffer.append(delta);text=''.join(buffer)
                while True:
                    match=re.search(r'\[[cC]\d+\]',text[emitted[0]:])
                    if not match:break
                    end=emitted[0]+match.end();paragraph=text[emitted[0]:end]
                    if '【选项】' in paragraph:break
                    if teacher_agent.paragraph_grounded(paragraph,tctx):
                        if ctx:ctx.stream(paragraph)
                        emitted[0]=end
                    else:break
            result=self.services.gateway.complete('teacher',request[1],system=request[0],on_text=stream,
                cancel=ctx.cancel if ctx else None,plan_id=course.plan_id,max_tokens=4096)
            try:
                teaching=teacher_agent.parse_teaching(result.text,tctx)
                self._teacher_request_page.accept(request[0],request[1],teacher_agent._dialogue(tctx['dialogue']),result.text)
                if ctx:
                    remaining=teaching.speech[emitted[0]:]
                    if remaining:ctx.stream(remaining)
                return teaching
            except AgentFormatError:
                if ctx:ctx.stream(RESET)
                if attempt==0:
                    request=(request[0],request[1]+[dict(role='user',content='上一条回复引用不合格。只依据本点段落，每一段写独立[c编号]；开场才给选项。重新写一次。')])
        mapping=teacher_agent.citation_map(tctx)
        speech='\n\n'.join(f"{excerpts[cid]} [{alias}]" for alias,cid in mapping.items())
        options=('我们一起看原文','我还不知道') if opening else ()
        if ctx:ctx.stream(speech)
        return teacher_agent.Teaching(speech,tuple(excerpts),options)

    def _deliver(self, course, state, teaching, user=None, explained=False):
        mapping={f'c{i}':cid for i,cid in enumerate(sorted(course.excerpts(self._focus(course,state))),1)}
        with course.store.transaction():
            if user is not None:self._user(course,state,user)
            self._say(course,state,_link_citations(teaching.speech,mapping),role='assistant',kind='teach')
            if explained:
                document=(course.store.records('teacher_profile',course.plan_id) or [{}])[0].get('document',{})
                points=dict(document.get('teaching_points',{}));pid=self._focus(course,state)[0]
                points[pid]=dict(point_id=pid,title=course.point(pid)['title'],chunk_ids=list(teaching.cited_chunks),at=self.now().isoformat())
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
        attempt=(state.get('test') or {}).get('attempt') if context in ('module_test','skip_test') else None
        angle=None
        if context=='check':
            used=course.book().point(pending['point_ids'][0]).used_angles;angle=ANGLES[len(used)%len(ANGLES)]
        event=Judgement(pending['answer_id'],course.next_seq(),self.now(),context,course.point(pending['point_ids'][0])['module_id'],attempt,tuple((pid,pid not in wrong) for pid in pending['point_ids']),angle)
        course.store.append_judgement(course.plan_id,event,details=dict(question=q['stem'],question_id=q['id'],question_type=q['kind'],answer=answer,reason=verdict.reason,point_checks=list(verdict.checks),source=q['source'],answer_source=q['answer_source']),credit_day=self._day(state) if context=='check' else None)

    def _answer_id(self, course, state, pending):return pending['answer_id']

    def _answer_fact(self, course, state, context, pending, verdict):
        # Casual grades already carry the stable answer_id in judgements;
        # question_answers is the existing detection/quick-confirm ledger.
        if context=='casual':return
        q=pending['question']
        course.store.append_question_answer(course.plan_id,pending['answer_id'],module_id=course.point(pending['point_ids'][0])['module_id'],context=context,
            attempt_id=(state.get('test') or {}).get('attempt'),question_id=q['id'],point_ids=pending['point_ids'],angle=None,
            correct=verdict.correct,wrong_points=verdict.wrong_points,reason=verdict.reason,checks=verdict.checks,at=self.now())

    def _credit_learning(self, course, state, point):
        course.store.add_credit(course.plan_id,f"learn:{point['id']}",point['id'],point['minutes'],self._day(state),'tentative')

    def _profile(self, course):
        document=(course.store.records('teacher_profile',course.plan_id) or [{}])[0].get('document',{})
        skipped={p['id'] for m in course.modules if course.book().module(m['id']).state=='skipped' for p in course.points_of(m['id'])}
        taught=set(document.get('teaching_points',{}))-skipped
        latest={}
        for row in sorted(course.store.records('judgements',course.plan_id),key=lambda r:r['event']['seq']):
            for pid,correct in row['event']['results']:
                if not correct:latest[pid]=row['details'].get('reason','')
        facts='；'.join(course.point(pid)['title']+'曾答错：'+reason for pid,reason in latest.items())
        text=f"已讲{len(taught)}个知识点；另有{len(skipped)}个点直接考通过、没有讲（全计划{len(course.points)}个点）。"
        if facts:text+='已记录的作答事实：'+facts
        course.store.save_teacher_profile(course.plan_id,dict(document,text=text,preferences=self._prefs(),
            weak_points=[dict(point_id=pid,note=reason) for pid,reason in latest.items()]))

    def _file(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        return AccountJSON(self.services.account_id, self.services.data_root, f"classroom/{plan_id}.json")


    @staticmethod
    def _test_already_closed(course, state):
        test = state.get("test")
        return bool(test and test.get("kind") in ("module_test", "skip_test") and test.get("attempt")
                    and course.book().attempt_closed(state["module_id"], test["attempt"]))


    def _course(self, plan_id):
        if not isinstance(plan_id, str) or not _PLAN_ID.fullmatch(plan_id):
            raise BridgeError("PARAM_INVALID", "计划编号无效。")
        store = self.services.store
        try:
            plan = store.get_plan(plan_id)
        except StoreError:
            raise BridgeError("PLAN_NOT_FOUND", "找不到这份计划。") from None
        if plan.get('unusable'):
            raise BridgeError('OLD_PLAN', '旧版计划，已不可用')
        if plan.get("state") not in ("active", "checking", "completed"):
            raise BridgeError("PLAN_NOT_STARTED", "这份计划还没开始，请先在新建计划里点“开始计划”。")
        return _Course(store, plan)


    def _today(self, course, state):
        from datetime import time as clock
        settings = self.services.settings.load()
        hour, minute = map(int, settings["refresh_time"].split(":"))
        now = self.now().astimezone()
        day = state.get("day")
        from datetime import date
        return course.store.today_status(course.plan_id, now=now, refresh=clock(hour, minute), tz=now.tzinfo,
                                         classroom_day=date.fromisoformat(day) if day else None)


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
            for m in course.modules:
                if not self._ready(course,m['id']):self._prepare(course,m['id'])
            return
        failed = [m for m in course.modules if book.module(m["id"]).state == "failed"]
        if failed:
            state.update(module_id=failed[0]["id"], index=len(course.points_of(failed[0]["id"])), step="test_done",
                         pending=None, test=None)
            self._say(course, state, "所有模块都学过了。这个模块还有没过的点，重测通过后才能进入最终检验。", role="system")
            return
        if not book.check_ready():
            self._say(course, state, '本次可学模块已结束。资料缺失的点已跳过，补齐资料后再检测；跳过不代表已掌握。', role='system')
            return
        state.update(step="check_offer", pending=None, test=None)
        self._prepare_final(course)
        self._say(course, state, "全部模块都通过了！接下来是最终检验：每个知识点换不同角度问，答对升级，"
                                 "全部升到最高级就完成整个计划。", role="system")


    def _complete(self, course, state, ctx):
        try:
            course.store.complete_plan(course.plan_id, today=self._day(state))
        except StoreError as exc:
            if exc.code != 'PLAN_NOT_COMPLETE': raise
            self._say(course, state, '还需要通过全部知识点的最终检验，才能完成计划。')
            return
        course.plan = course.store.get_plan(course.plan_id)
        state.update(step="completed", pending=None, check=None)
        self._say(course, state, "最终检验全部通过，这份计划完成了！可以在「我的计划」里查看学习报告。")


    def _prefs(self):
        settings = self.services.settings.load()
        return {key: settings[key] for key in PREF_KEYS}


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
        test, module_id = state["test"], state["module_id"]
        points = course.points_of(module_id)
        day = self._day(state)
        state["pending"], state["test"] = None, None
        store = course.store
        answered = any(r.get('attempt_id') == test['attempt'] for r in store.records('question_answers', course.plan_id))
        answered = answered or any(r['event'].get('attempt_id') == test['attempt']
                                   for r in store.records('judgements', course.plan_id))
        if not test['queue'] or not answered:
            skipped = state.setdefault('skipped_modules', [])
            if module_id not in skipped: skipped.append(module_id)
            state['step'] = 'test_done'
            self._say(course, state, '本轮没有可用题目，已记录资料缺失并跳过。可以继续后续模块；没有记入答题、时长或通过。', role='system')
            return
        # A crash after the close but before the room state was saved leaves the attempt closed in the
        # store; finishing again must not close it twice (BUG-23). The steps below are idempotent.
        if test["kind"] != "retest":
            if not course.book().attempt_closed(module_id, test["attempt"]):
                store.append_attempt_closed(course.plan_id, AttemptClosed(uuid.uuid4().hex, course.next_seq(), module_id,
                                                                          test["attempt"], test["kind"]))
            if "final_check_minutes" in course.module(module_id):
                store.settle_module_test(course.plan_id, module_id, test["attempt"], today=day)
        book = course.book()
        states = {p["id"]: book.point(p["id"]).state for p in points}
        module_state = book.module(module_id).state
        if test["kind"] == "skip_test" and module_state == "untested":   # below 90%: learn it normally
            state["step"], state["index"] = "teach", 0
            self._say(course, state, "直接考没有达到90%，这个模块正常学；点开始上课。")
            return
        if test["kind"] == "skip_test":
            for p in points:  # the whole module's time counts on the day of the direct test (TEACH-07)
                store.add_credit(course.plan_id, f"learn:{p['id']}", p["id"], p["minutes"], day, "confirmed")
        if test["kind"] in ("module_test", "skip_test"):
            if "final_check_minutes" not in course.module(module_id):
                store.settle_module_test(course.plan_id, module_id, test["attempt"], today=day)
            for p in points:
                if states[p["id"]] == "red":
                    store.revoke_credits(course.plan_id, p["id"], today=day)
                else:
                    store.confirm_credits(course.plan_id, p["id"], today=day)
        else:
            for p in points:  # a red point answered right in a retest is learnt again: its time counts now
                if states[p["id"]] == "green" and p["id"] in test["targets"]:
                    store.add_credit(course.plan_id, f"relearn:{p['id']}", p["id"], p["minutes"], day, "confirmed")
        red = [p["title"] for p in points if states[p["id"]] == "red"]
        weak = [p["title"] for p in points if states[p["id"]] == "weak"]
        more = any(book.module(m["id"]).state == "untested" for m in course.modules if m["id"] != module_id)
        onward = "学下一个模块" if more else "结束模块学习、进入最终检验"
        mastered = sum(1 for p in points if states[p["id"]] == "green")
        state["step"] = "test_done"
        self._say(course, state, f"本轮结束：{mastered}/{len(points)}个点掌握。" +
                  (f"需要重测：{'、'.join(red)}。" if red else "这个模块已通过。"))
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
        self._chunks = None
        if not self.modules or not self.points:
            raise BridgeError("PLAN_EMPTY", "这份计划还没有可学的内容。")

    def module(self, module_id):
        return next(m for m in self.modules if m["id"] == module_id)

    def points_of(self, module_id):
        return [p for p in self.points if p["module_id"] == module_id]

    def point(self, point_id):
        return next(p for p in self.points if p["id"] == point_id)

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
        chunks={c['id']:c for c in self.store.records('source_chunks',self.plan_id)}
        sources={s['id']:s for s in self.store.records('sources',self.plan_id)}
        wanted=list(dict.fromkeys(cid for pid in point_ids for cid in self.point(pid).get('chunk_ids',[])))
        return {cid:chunks[cid] for cid in wanted if cid in chunks and chunks[cid].get('text','').strip()
                and sources.get(chunks[cid]['source_id'],{}).get('adopted')}

    def excerpts(self, point_ids):
        result={cid:row['text'][:40000] for cid,row in self.passages(point_ids).items()}
        if not result:raise BridgeError('NO_MATERIAL','这个知识点没有关联到已采用的原文，请在计划里补充资料。')
        return result

    def passage_card(self, chunk_id):
        chunk=next(c for c in self.store.records('source_chunks',self.plan_id) if c['id']==chunk_id)
        source=next(s for s in self.store.records('sources',self.plan_id) if s['id']==chunk['source_id'])
        return dict(id=chunk_id,text=chunk['text'],title=source.get('title') or source.get('filename') or '资料',
                    url=source.get('url'),locator=' / '.join(chunk.get('section') or []) or f"段落 {chunk.get('position',0)+1}")


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


def register(bridge, services, study_day, *, presence=lambda active,plan_id=None:None):
    rooms={}
    def room():
        current=services();key=id(current)
        if key not in rooms:
            for old in rooms.values():old.cancel_materials()
            rooms.clear()
            rooms[key]=Classroom(current,study_day)
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
    methods={
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
        'classroom.next_module':lambda p,c:invoke('next_module',p,c),
        'classroom.check':lambda p,c:invoke('start_check',p,c),
        'classroom.practice':lambda p,c:invoke('start_practice',p,c,p.get('module_id'),p.get('point_id'),p.get('count',5)),
    }
    for name,fn in methods.items():bridge.register(name,fn)
