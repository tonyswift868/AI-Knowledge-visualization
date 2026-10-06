"""Deterministic mastery projection from immutable, ID-idempotent events.

Equal sequence numbers use event_id as a deterministic tie-breaker. Final
answers affect completion only after module prerequisites exist in replay.
Assessment is keyed by question_id; points are display projections only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import copy

CONTEXTS = ('casual', 'quick_confirm', 'module_test', 'retest', 'skip_test', 'check')


class MasteryError(ValueError):
    def __init__(self, code, message='掌握度事件或查询参数无效。'):
        self.code, self.message = code, message
        super().__init__(message)


def _id(value):
    if not isinstance(value, str) or not value.strip():
        raise MasteryError('INVALID_ID')


def _event_base(event):
    _id(event.event_id); _id(event.module_id)
    if type(event.seq) is not int or event.seq < 0:
        raise MasteryError('INVALID_SEQUENCE')


@dataclass(frozen=True)
class PointRef:
    point_id: str
    module_id: str

    def __post_init__(self):
        _id(self.point_id); _id(self.module_id)


@dataclass(frozen=True)
class Judgement:
    event_id: str
    seq: int
    at: datetime
    context: str
    module_id: str
    attempt_id: str | None = None
    results: tuple[tuple[str, bool], ...] = ()
    angle: str | None = None
    question_id: str | None = None

    def __post_init__(self):
        _event_base(self)
        if not isinstance(self.at, datetime) or self.at.tzinfo is None or self.at.utcoffset() is None:
            raise MasteryError('NAIVE_DATETIME', '判定时间必须带时区。')
        if self.context not in CONTEXTS:
            raise MasteryError('INVALID_CONTEXT')
        if self.context in ('module_test', 'skip_test'):
            _id(self.attempt_id)
        elif self.attempt_id is not None:
            _id(self.attempt_id)
        if self.question_id is not None:
            _id(self.question_id)
        if self.angle is not None and not isinstance(self.angle, str):
            raise MasteryError('INVALID_ANGLE')
        try:
            results = tuple(tuple(pair) for pair in self.results)
            seen = set()
            for point_id, correct in results:
                _id(point_id)
                if type(correct) is not bool or point_id in seen:
                    raise ValueError()
                seen.add(point_id)
            if not results:
                raise ValueError()
        except (TypeError, ValueError):
            raise MasteryError('INVALID_RESULTS', '一题各知识点须分别提供一次布尔判定。') from None
        object.__setattr__(self, 'results', results)


@dataclass(frozen=True)
class AttemptClosed:
    event_id: str
    seq: int
    module_id: str
    attempt_id: str
    kind: str

    def __post_init__(self):
        _event_base(self); _id(self.attempt_id)
        if self.kind not in ('module_test', 'skip_test', 'retest'):
            raise MasteryError('INVALID_CONTEXT')


def event_to_dict(event):
    data = asdict(event)
    data['type'] = 'judgement' if isinstance(event, Judgement) else 'attempt_closed'
    if isinstance(event, Judgement):
        data['at'] = event.at.isoformat()
        data['results'] = [list(pair) for pair in event.results]
    return data


def event_from_dict(data):
    try:
        data = dict(data)
        kind = data.pop('type')
        if kind == 'judgement':
            data['at'] = datetime.fromisoformat(data['at'])
            return Judgement(**data)
        if kind == 'attempt_closed':
            return AttemptClosed(**data)
        raise ValueError()
    except (TypeError, ValueError, KeyError):
        raise MasteryError('INVALID_EVENT', '无法读取掌握度事件。') from None


@dataclass(frozen=True)
class QuestionState:
    question_id: str
    module_id: str
    ever_wrong: bool
    streak: int
    passed: bool
    module_correct: bool | None
    check_questions: int
    check_correct: int
    last_question: int | None


@dataclass(frozen=True)
class PointState:
    point_id: str
    module_id: str
    state: str
    casual_errors: int = 0
    module_errors: int = 0
    level: int = 1  # Compatibility display: pending=1 / all associated questions passed=3. No level upgrades.
    check_questions: int = 0
    check_correct: int = 0
    last_question: int | None = None


@dataclass(frozen=True)
class ModuleState:
    module_id: str
    state: str
    point_ids: tuple[str, ...]
    module_test_count: int
    skip_test_count: int


class MasteryBook:
    """Replay immutable question outcomes. Free practice never changes completion."""
    def __init__(self, points, questions=(), taught=()):
        self._points = tuple(p if isinstance(p, PointRef) else PointRef(**p) for p in points)
        self._refs = {p.point_id:p for p in self._points}
        if len(self._refs)!=len(self._points):raise MasteryError('DUPLICATE_POINT')
        self._modules = dict.fromkeys(p.module_id for p in self._points)
        self._questions = {q['id']:{k:copy.deepcopy(q[k]) for k in ('id','module_id','point_ids')} for q in questions}
        self._taught = set(taught)
        self._events = {}
        self._cache = None

    def apply(self, event):
        if isinstance(event,dict):event=event_from_dict(event)
        if not isinstance(event,(Judgement,AttemptClosed)):raise MasteryError('INVALID_EVENT')
        previous=self._events.get(event.event_id)
        if previous is not None:
            if event_to_dict(previous)!=event_to_dict(event):raise MasteryError('EVENT_CONFLICT')
            return
        if event.module_id not in self._modules:raise MasteryError('UNKNOWN_MODULE')
        if isinstance(event,Judgement):
            for pid,_ in event.results:
                if pid not in self._refs or self._refs[pid].module_id!=event.module_id:raise MasteryError('POINT_MODULE_MISMATCH')
            if event.question_id is not None:
                q=self._questions.get(event.question_id)
                if not q or q['module_id']!=event.module_id:raise MasteryError('UNKNOWN_QUESTION')
        if isinstance(event,AttemptClosed) and self.attempt_closed(event.module_id,event.attempt_id):
            raise MasteryError('ATTEMPT_ALREADY_CLOSED')
        self._events[event.event_id]=event;self._cache=None

    def attempt_closed(self, module_id, attempt_id):
        return any(isinstance(e,AttemptClosed) and (e.module_id,e.attempt_id)==(module_id,attempt_id) for e in self._events.values())

    def _project(self):
        if self._cache is not None:return self._cache
        questions={qid:dict(ever_wrong=False,streak=0,passed=False,module_correct=None,check_questions=0,
            check_correct=0,last_question=None,module_errors=0,check_errors_before_pass=0,check_passed_at=None) for qid in self._questions}
        modules={mid:dict(state='untested',module_test_count=0,skip_test_count=0,retest_count=0,first_test=None,
            skip_test_passed=False) for mid in self._modules}
        for mid,m in modules.items():
            if not any(q['module_id']==mid for q in self._questions.values()) and all(p.point_id in self._taught for p in self._points if p.module_id==mid):
                m['state']='passed'
        attempts={};closed=set();history=[]
        for event in sorted(self._events.values(),key=lambda e:(e.seq,e.event_id)):
            m=modules[event.module_id];key=(event.module_id,event.attempt_id)
            if isinstance(event,AttemptClosed):
                answers=attempts.get(key,{})
                ids=[qid for qid,q in self._questions.items() if q['module_id']==event.module_id]
                passed=bool(ids) and sum(questions[qid]['module_correct'] is True for qid in ids)*10>=len(ids)*9
                if event.kind=='retest':m['retest_count']+=1
                else:m[event.kind+'_count']+=1
                if m['first_test'] is None and event.kind!='retest':
                    m['first_test']=dict(event_id=event.event_id,attempt_id=event.attempt_id,kind=event.kind,
                        correct_questions=sum(answers.get(qid,False) for qid in ids),total_questions=len(ids),
                        mastery_ratio=sum(answers.get(qid,False) for qid in ids)/len(ids) if ids else 0)
                direct=event.kind=='skip_test' or event.kind=='retest' and m['skip_test_count']>0 and m['module_test_count']==0
                m['state']=('skipped' if direct else 'passed') if passed else 'failed'
                if direct and passed:m['skip_test_passed']=True
                closed.add(key);continue
            qid=event.question_id
            if qid not in questions:continue  # Keep old events readable; never reinterpret them as new assessment.
            q=questions[qid];correct=all(v for _,v in event.results)
            if event.context in ('module_test','skip_test','retest') and key not in closed:
                attempts.setdefault(key,{})[qid]=correct
                q['module_correct']=correct
                if not correct:q['ever_wrong']=True;q['module_errors']+=1
            elif event.context=='check' and all(m['state'] in ('passed','skipped') for m in modules.values()):
                history.append(qid);q['last_question']=len(history)-1
                q['check_questions']+=1;q['check_correct']+=int(correct)
                if not q['passed'] or not correct:
                    if correct:q['streak']+=1
                    else:q['ever_wrong']=True;q['streak']=0;q['check_errors_before_pass']+=1;q['check_passed_at']=None
                    q['passed']=q['streak'] >= (2 if q['ever_wrong'] else 1)
                    if q['passed']:q['check_passed_at']=event.at.isoformat()
        self._cache=questions,modules,history
        return self._cache

    def question(self, question_id):
        qs,_,_=self._project()
        if question_id not in qs:raise MasteryError('UNKNOWN_QUESTION')
        q=qs[question_id]
        return QuestionState(question_id,self._questions[question_id]['module_id'],**{k:q[k] for k in
            ('ever_wrong','streak','passed','module_correct','check_questions','check_correct','last_question')})

    def point(self, point_id):
        if point_id not in self._refs:raise MasteryError('UNKNOWN_POINT')
        qs,ms,_=self._project();mid=self._refs[point_id].module_id
        rows=[qs[qid] for qid,q in self._questions.items() if point_id in q.get('point_ids',[])]
        state=('green' if rows and all(q['module_correct'] is True for q in rows) else
               'red' if ms[mid]['state']=='failed' and any(q['module_correct'] is False for q in rows) else
               'weak' if any(q['module_correct'] is False for q in rows) else 'untested')
        if not rows and point_id in self._taught:state='green'
        casual_errors=sum(not correct for event in self._events.values() if isinstance(event,Judgement)
                          and event.context in ('casual','quick_confirm') for pid,correct in event.results if pid==point_id)
        return PointState(point_id,mid,state,casual_errors=casual_errors,module_errors=sum(q['module_errors'] for q in rows),
            level=3 if rows and all(q['passed'] for q in rows) or not rows and point_id in self._taught else 1,
            check_questions=sum(q['check_questions'] for q in rows),check_correct=sum(q['check_correct'] for q in rows),
            last_question=max((q['last_question'] for q in rows if q['last_question'] is not None),default=None))

    def module(self, module_id):
        _,ms,_=self._project()
        if module_id not in ms:raise MasteryError('UNKNOWN_MODULE')
        m=ms[module_id]
        return ModuleState(module_id,m['state'],tuple(p.point_id for p in self._points if p.module_id==module_id),
            m['module_test_count'],m['skip_test_count'])

    def check_ready(self):
        return bool(self._modules) and all(self.module(mid).state in ('passed','skipped') for mid in self._modules)

    def check_complete(self):
        qs,_,_=self._project()
        return self.check_ready() and all(q['passed'] for q in qs.values())

    def next_check_questions(self, min_gap=3):
        if type(min_gap) is not int or min_gap<0:raise MasteryError('INVALID_QUERY')
        if not self.check_ready():return []
        qs,_,history=self._project();order={qid:i for i,qid in enumerate(qs)}
        pending=[qid for qid,q in qs.items() if not q['passed']]
        if not pending:return []
        def rank(qid):
            last=qs[qid]['last_question'];gap=len(history)-last-1 if last is not None else len(history)+min_gap
            return (gap<min_gap,last is not None,last if last is not None else -1,order[qid])
        eligible=[qid for qid in pending if not rank(qid)[0]]
        if eligible:return sorted(eligible,key=rank)
        # Passed questions can separate repeats too. A small bank rotates all
        # other questions before returning to the still-pending one.
        others=[qid for qid in qs if not history or qid!=history[-1]]
        return sorted(others or pending,key=lambda qid:(qs[qid]['last_question'] is not None,
            qs[qid]['last_question'] if qs[qid]['last_question'] is not None else -1,order[qid]))

    def stats(self):
        qs,ms,history=self._project()
        return dict(check_questions=len(history),questions={qid:dict(q) for qid,q in qs.items()},
            points={p.point_id:dict(casual_errors=self.point(p.point_id).casual_errors,module_errors=self.point(p.point_id).module_errors,
                check_questions=self.point(p.point_id).check_questions,check_correct=self.point(p.point_id).check_correct,
                check_accuracy=self.point(p.point_id).check_correct/self.point(p.point_id).check_questions if self.point(p.point_id).check_questions else 0)
                for p in self._points},modules={mid:dict(m,test_count=m['module_test_count']+m['skip_test_count']) for mid,m in ms.items()})

    def report_stats(self):
        result=self.stats();qs,_,_=self._project()
        for pid,p in result['points'].items():
            rows=[qs[qid] for qid,q in self._questions.items() if pid in q.get('point_ids',[])]
            p.update(ever_weak=any(q['ever_wrong'] for q in rows),ever_red=any(q['ever_wrong'] for q in rows),
                check_errors_before_pass=sum(q['check_errors_before_pass'] for q in rows),
                check_passed_at=max((q['check_passed_at'] for q in rows if q['check_passed_at']),default=None))
        return result

    def to_dict(self):
        return dict(version=2,points=[asdict(p) for p in self._points],questions=list(self._questions.values()),taught=sorted(self._taught),
            events=[event_to_dict(e) for e in sorted(self._events.values(),key=lambda e:(e.seq,e.event_id))])

    @classmethod
    def from_dict(cls,data):
        try:
            if data['version']!=2:raise ValueError()
            book=cls(data['points'],data['questions'],data['taught'])
            for event in data['events']:book.apply(event)
            return book
        except (ValueError,TypeError,KeyError):raise MasteryError('INVALID_DATA') from None
