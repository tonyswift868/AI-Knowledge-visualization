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
    def __init__(self, points, questions=(), taught=(), *, historical_event_ids=(),
                 final_question_ids=None):
        self._points = tuple(p if isinstance(p, PointRef) else PointRef(**p) for p in points)
        self._refs = {p.point_id:p for p in self._points}
        if len(self._refs)!=len(self._points):raise MasteryError('DUPLICATE_POINT')
        self._modules = dict.fromkeys(p.module_id for p in self._points)
        self._questions = {q['id']:{k:copy.deepcopy(q[k]) for k in ('id','module_id','point_ids','origin','role') if k in q}
                           for q in questions}
        self._final_question_ids = (set(self._questions) if final_question_ids is None
                                    else set(final_question_ids))
        if not self._final_question_ids <= set(self._questions):
            raise MasteryError('UNKNOWN_QUESTION')
        self._taught = set(taught)
        self._events = {}
        self._event_details = {}
        self._historical_event_ids = set(historical_event_ids)
        self._cache = None

    @classmethod
    def from_course_records(cls, points, modules, bank, taught, event_rows, answer_rows=()):
        """Build the same projection from plain record payloads (including RO snapshots)."""
        from .question_bank import assessment_questions
        module_order={m['id']:i for i,m in enumerate(modules)}
        ordered_points=sorted(points,key=lambda p:(module_order.get(p['module_id'],10**9),p['position'],p['id']))
        current=assessment_questions(bank)
        final_ids={q['id'] for q in current}
        questions=[q for q in bank if q.get('module_id') in module_order and isinstance(q.get('point_ids'),list)]
        known={q['id'] for q in questions}
        for row in answer_rows:
            qid=row.get('question_id')
            if qid and qid not in known and row.get('module_id') in module_order:
                questions.append(dict(id=qid,module_id=row['module_id'],point_ids=list(row.get('point_ids') or [])))
                known.add(qid)
        events=[];details={}
        for row in event_rows:
            event=event_from_dict(row['event']);events.append(event)
            row_details=row.get('details') or {}
            if isinstance(event,AttemptClosed):
                manifest={k:row_details[k] for k in ('assessment_id','question_ids') if k in row_details}
                if manifest:details[event.event_id]=manifest
            elif row_details.get('user_override') is True:
                details[event.event_id]=copy.deepcopy(row_details)
            if isinstance(event,Judgement) and event.question_id and event.question_id not in known:
                questions.append(dict(id=event.question_id,module_id=event.module_id,
                                      point_ids=[pid for pid,_ in event.results]))
                known.add(event.question_id)
        for event in events:
            if isinstance(event,AttemptClosed):
                for qid in details.get(event.event_id,{}).get('question_ids',[]):
                    if qid not in known:
                        questions.append(dict(id=qid,module_id=event.module_id,point_ids=[]));known.add(qid)
        # Only question-less events use the pre-question point reducer. A qid
        # can be absent from today's final-check pool while still belonging to
        # a frozen module assessment manifest (including duplicate/retired rows).
        legacy=[e for e in events if isinstance(e,Judgement) and e.question_id is None]
        legacy_attempts={(e.module_id,e.attempt_id) for e in legacy if e.context in ('module_test','skip_test','retest')}
        historical={e.event_id for e in legacy}
        historical.update(e.event_id for e in events if isinstance(e,AttemptClosed)
                          and (e.module_id,e.attempt_id) in legacy_attempts)
        book=cls((PointRef(p['id'],p['module_id']) for p in ordered_points),questions,taught,
                 historical_event_ids=historical,final_question_ids=final_ids)
        for event in events:book.apply(event,details=details.get(event.event_id))
        return book

    def apply(self, event, *, details=None):
        if isinstance(event,dict):event=event_from_dict(event)
        if not isinstance(event,(Judgement,AttemptClosed)):raise MasteryError('INVALID_EVENT')
        previous=self._events.get(event.event_id)
        if previous is not None:
            if event_to_dict(previous)!=event_to_dict(event):raise MasteryError('EVENT_CONFLICT')
            prior_details = self._event_details.get(event.event_id, {})
            if details is not None and prior_details != details:raise MasteryError('EVENT_CONFLICT')
            return
        if event.module_id not in self._modules:raise MasteryError('UNKNOWN_MODULE')
        if isinstance(event,Judgement):
            for pid,_ in event.results:
                if pid not in self._refs or self._refs[pid].module_id!=event.module_id:raise MasteryError('POINT_MODULE_MISMATCH')
            if event.question_id is not None:
                q=self._questions.get(event.question_id)
                if (not q or q['module_id']!=event.module_id) and event.event_id not in self._historical_event_ids:
                    raise MasteryError('UNKNOWN_QUESTION')
        if isinstance(event,AttemptClosed) and self.attempt_closed(event.module_id,event.attempt_id):
            raise MasteryError('ATTEMPT_ALREADY_CLOSED')
        if details is not None and not isinstance(details, dict):raise MasteryError('INVALID_EVENT_DETAILS')
        self._events[event.event_id]=event
        if details:self._event_details[event.event_id]=copy.deepcopy(details)
        self._cache=None

    def preview_question_override(self, question_ids, *, kind='question_passed'):
        """Purely project an explicit pass grant without creating an event.

        This is used by the one-time administrator tool's read-only preview.
        It deliberately changes no answer, error, check-count, or credit data.
        """
        if kind not in ('question_passed', 'final_check_passed'):
            raise MasteryError('INVALID_OVERRIDE')
        ids=list(dict.fromkeys(question_ids))
        if not ids or any(qid not in self._questions for qid in ids):
            raise MasteryError('UNKNOWN_QUESTION')
        questions,modules,_=self._project()
        projected={qid:dict(row) for qid,row in questions.items()}
        projected_modules={mid:dict(row) for mid,row in modules.items()}
        for qid in ids:
            if kind=='question_passed':
                projected[qid].update(passed=True,streak=max(1,projected[qid]['streak']))
                if projected[qid]['module_correct'] is False:
                    projected[qid]['module_correct']=True
            else:
                projected[qid].update(passed=True,streak=max(1,projected[qid]['streak']))
        if kind=='question_passed':
            for mid,module in projected_modules.items():
                target=[qid for qid in module.get('assessment_ids',()) if qid in projected]
                if (module['state']=='failed' and target and
                        sum(projected[qid]['module_correct'] is True for qid in target)*10>=len(target)*9):
                    module['state']='passed'
        return {'questions':{qid:projected[qid] for qid in ids},'modules':projected_modules}

    def attempt_closed(self, module_id, attempt_id):
        return any(isinstance(e,AttemptClosed) and (e.module_id,e.attempt_id)==(module_id,attempt_id) for e in self._events.values())

    def _project(self):
        if self._cache is not None:return self._cache
        questions={qid:dict(ever_wrong=False,streak=0,passed=False,module_correct=None,check_questions=0,
            check_correct=0,last_question=None,module_errors=0,check_errors_before_pass=0,check_passed_at=None) for qid in self._questions}
        modules={mid:dict(state='untested',module_test_count=0,skip_test_count=0,retest_count=0,first_test=None,
            skip_test_passed=False) for mid in self._modules}
        # Only genuinely point-only legacy events feed the old point reducer.
        # Retired question events retain their question identity and frozen
        # assessment; never reinterpret them as point-level judgements.
        legacy_attempts={(e.module_id,e.attempt_id) for e in self._events.values()
                         if isinstance(e,Judgement) and e.event_id in self._historical_event_ids
                         and e.question_id is None and e.context in ('module_test','skip_test','retest')}
        point_history = [e for e in self._events.values() if e.event_id in self._historical_event_ids and
                         (isinstance(e, Judgement) and e.question_id is None or
                          isinstance(e, AttemptClosed) and (e.module_id,e.attempt_id) in legacy_attempts)]
        old_points, old_modules = _historical_progress(self._points, point_history)
        self._historical_points = old_points
        for mid, prior in old_modules.items(): modules[mid].update(prior)
        closed_modules={e.module_id for e in self._events.values() if isinstance(e,AttemptClosed)
                        and e.event_id not in self._historical_event_ids}
        for mid,m in modules.items():
            has_current_questions=any(qid in self._final_question_ids and q['module_id']==mid
                                      for qid,q in self._questions.items())
            if (mid not in old_modules and mid not in closed_modules and not has_current_questions
                    and all(p.point_id in self._taught for p in self._points if p.module_id==mid)):
                m['state']='passed'
        attempts={};closed=set();history=[]
        for event in sorted(self._events.values(),key=lambda e:(e.seq,e.event_id)):
            if event.event_id in self._historical_event_ids: continue
            m=modules[event.module_id];key=(event.module_id,event.attempt_id)
            if isinstance(event,AttemptClosed):
                answers=attempts.get(key,{})
                details=self._event_details.get(event.event_id,{})
                assessment_id=details.get('assessment_id')
                manifest=details.get('question_ids')
                if event.kind=='retest':
                    # Retests revisit the original module-test manifest. Older
                    # records have no details, so the first test's judged IDs
                    # provide a deterministic frozen denominator.
                    if not m.get('assessment_ids'):
                        m['assessment_ids']=tuple(sorted(qid for qid,q in self._questions.items()
                            if q['module_id']==event.module_id and q['module_correct'] is not None))
                    ids=list(m.get('assessment_ids',()))
                else:
                    ids=list(manifest) if isinstance(manifest,list) else sorted(answers)
                    if not ids:
                        ids=[qid for qid,q in self._questions.items()
                             if qid in self._final_question_ids and q['module_id']==event.module_id]
                    if event.kind in ('module_test','skip_test'):
                        m['assessment_id']=assessment_id or event.attempt_id
                        m['assessment_ids']=tuple(dict.fromkeys(ids))
                ids=[qid for qid in ids if qid in questions]
                # Keep the frozen manifest and first-test summary intact for
                # history, while removing retired generated cloze from the
                # current mastery denominator.
                active_ids=[qid for qid in ids if not self._is_retired_cloze(qid)]
                cloze_only_manifest=bool(ids) and all(self._is_retired_cloze(qid) for qid in ids)
                # No-question direct review is a learner's skip decision, not
                # an assessment. Credits and this close are committed together;
                # retain the existing skipped projection without fake answers.
                if not ids and event.kind=='skip_test' and all(p.point_id in self._taught for p in self._points if p.module_id==event.module_id):
                    m.update(state='skipped',skip_test_passed=True)
                    closed.add(key)
                    continue
                historical_passed=(bool(ids) and
                    sum(questions[qid]['module_correct'] is True for qid in ids)*10>=len(ids)*9)
                passed=historical_passed or (bool(active_ids) and
                    sum(questions[qid]['module_correct'] is True for qid in active_ids)*10>=len(active_ids)*9)
                if not active_ids and all(p.point_id in self._taught for p in self._points
                                          if p.module_id==event.module_id):
                    passed=True
                elif cloze_only_manifest and m['state'] not in ('passed','skipped'):
                    # A former cloze-only assessment is no longer a learning
                    # gate. Until its points are taught, resume teaching while
                    # preserving the historical failed attempt below.
                    m['state']='untested'
                if event.kind=='retest':m['retest_count']+=1
                else:m[event.kind+'_count']+=1
                if m['first_test'] is None and event.kind!='retest':
                    m['first_test']=dict(event_id=event.event_id,attempt_id=event.attempt_id,kind=event.kind,
                        correct_questions=sum(answers.get(qid,False) for qid in ids),total_questions=len(ids),
                        mastery_ratio=sum(answers.get(qid,False) for qid in ids)/len(ids) if ids else 0)
                direct=event.kind=='skip_test' or event.kind=='retest' and m['skip_test_count']>0 and m['module_test_count']==0
                # A completed module assessment is a durable achievement.
                # New bank questions are for final check and cannot revoke it.
                if m['state'] not in ('passed','skipped') and not cloze_only_manifest:
                    m['state']=('skipped' if direct else 'passed') if passed else 'failed'
                elif cloze_only_manifest and passed and m['state'] not in ('passed','skipped'):
                    m['state']='skipped' if direct else 'passed'
                if direct and passed:m['skip_test_passed']=True
                closed.add(key);continue
            qid=event.question_id
            if qid not in questions:continue  # Keep old events readable; never reinterpret them as new assessment.
            details=self._event_details.get(event.event_id,{})
            if details.get('user_override') is True:
                # Apply at its replay position: checks after an administrator
                # grant must see the newly passed module, while earlier checks
                # must remain gated. This is mastery metadata, not an answer.
                kind=details.get('override_kind')
                q=questions[qid]
                if kind=='question_passed':
                    q.update(passed=True,streak=max(1,q['streak']))
                    if q['module_correct'] is False:
                        q['module_correct']=True
                    target=[target_id for target_id in m.get('assessment_ids',()) if target_id in questions]
                    if target and sum(questions[target_id]['module_correct'] is True for target_id in target)*10>=len(target)*9:
                        m['state']='passed'
                elif kind=='final_check_passed':
                    q.update(passed=True,streak=max(1,q['streak']))
                continue
            q=questions[qid];correct=all(v for _,v in event.results)
            if event.context in ('module_test','skip_test','retest') and key not in closed:
                attempts.setdefault(key,{})[qid]=correct
                q['module_correct']=correct
                if not correct:q['ever_wrong']=True;q['module_errors']+=1
            elif event.context=='check' and all(m['state'] in ('passed','skipped') for m in modules.values()):
                history.append(qid);q['last_question']=len(history)-1
                q['check_questions']+=1;q['check_correct']+=int(correct)
                # Old clients could re-ask a passed question as a spacing
                # filler. Keep that immutable answer in history and statistics,
                # but never let a stale post-pass answer revoke mastery.
                if not q['passed']:
                    if correct:q['streak']+=1
                    else:q['ever_wrong']=True;q['streak']=0;q['check_errors_before_pass']+=1;q['check_passed_at']=None
                    q['passed']=q['streak'] >= (2 if q['ever_wrong'] else 1)
                    if q['passed']:q['check_passed_at']=event.at.isoformat()
        self._cache=questions,modules,history
        return self._cache

    def _is_retired_cloze(self, question_id):
        row=self._questions.get(question_id,{})
        return row.get('origin')=='cloze' and row.get('role')=='retired'

    def question(self, question_id):
        qs,_,_=self._project()
        if question_id not in qs:raise MasteryError('UNKNOWN_QUESTION')
        q=qs[question_id]
        return QuestionState(question_id,self._questions[question_id]['module_id'],**{k:q[k] for k in
            ('ever_wrong','streak','passed','module_correct','check_questions','check_correct','last_question')})

    def point(self, point_id):
        if point_id not in self._refs:raise MasteryError('UNKNOWN_POINT')
        qs,ms,_=self._project();mid=self._refs[point_id].module_id
        all_rows=[qs[qid] for qid,q in self._questions.items() if point_id in q.get('point_ids',[])]
        module_rows=[row for row in all_rows if row['module_correct'] is not None]
        # Preserve the module result when a later bank refresh adds untouched
        # final-check questions. Once answered, those questions join the level
        # projection and a new wrong answer can still lower it.
        state_rows=module_rows if ms[mid]['state'] in ('passed','skipped') and module_rows else all_rows
        state=('green' if state_rows and all(q['module_correct'] is True for q in state_rows) else
               'red' if ms[mid]['state']=='failed' and any(q['module_correct'] is False for q in state_rows) else
               'weak' if any(q['module_correct'] is False for q in state_rows) else 'untested')
        if not state_rows and point_id in self._taught:state='green'
        level_rows=[row for row in all_rows if row['module_correct'] is not None or row['check_questions']>0 or row['passed']]
        if ms[mid]['state'] in ('passed','skipped') and level_rows:
            level_source=level_rows
        else:
            level_source=all_rows
        casual_errors=sum(not correct for event in self._events.values() if isinstance(event,Judgement)
                          and event.context in ('casual','quick_confirm') for pid,correct in event.results if pid==point_id)
        prior = self._historical_points.get(point_id)
        if prior and not any(q['module_correct'] is not None or q['check_questions'] for q in all_rows):
            # Keep attained mastery visible, without turning old questions into
            # current assessment/statistics or manufacturing correct answers.
            return PointState(point_id,mid,prior['state'],casual_errors=casual_errors,
                module_errors=prior['module_errors'],level=prior['level'])
        return PointState(point_id,mid,state,casual_errors=casual_errors,module_errors=sum(q['module_errors'] for q in all_rows),
            level=3 if level_source and all(q['passed'] for q in level_source) or not level_source and point_id in self._taught else 1,
            check_questions=sum(q['check_questions'] for q in all_rows),check_correct=sum(q['check_correct'] for q in all_rows),
            last_question=max((q['last_question'] for q in all_rows if q['last_question'] is not None),default=None))

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
        return self.check_ready() and all(qs[qid]['passed'] for qid in self._final_question_ids)

    def next_check_questions(self, min_gap=3):
        if type(min_gap) is not int or min_gap<0:raise MasteryError('INVALID_QUERY')
        if not self.check_ready():return []
        qs,_,history=self._project();order={qid:i for i,qid in enumerate(qs)}
        pending=[qid for qid in self._final_question_ids if not qs[qid]['passed']]
        if not pending:return []
        def rank(qid):
            last=qs[qid]['last_question'];gap=len(history)-last-1 if last is not None else len(history)+min_gap
            return (gap<min_gap,last is not None,last if last is not None else -1,order[qid])
        eligible=[qid for qid in pending if not rank(qid)[0]]
        if eligible:return sorted(eligible,key=rank)
        # Passed questions are outside the pending pool, even as spacing
        # fillers. Repeat the sole remaining question immediately; otherwise
        # keep rotating among pending questions when the preferred gap cannot
        # be met.
        if len(pending)==1:return pending
        others=[qid for qid in pending if not history or qid!=history[-1]]
        return sorted(others or pending,key=lambda qid:(qs[qid]['last_question'] is not None,
            qs[qid]['last_question'] if qs[qid]['last_question'] is not None else -1,order[qid]))

    def stats(self):
        qs,ms,history=self._project()
        return dict(check_questions=len(history),
            questions={qid:dict(qs[qid]) for qid in self._final_question_ids},
            points={p.point_id:dict(casual_errors=self.point(p.point_id).casual_errors,module_errors=self.point(p.point_id).module_errors,
                check_questions=self.point(p.point_id).check_questions,check_correct=self.point(p.point_id).check_correct,
                check_accuracy=self.point(p.point_id).check_correct/self.point(p.point_id).check_questions if self.point(p.point_id).check_questions else 0)
                for p in self._points},modules={mid:dict(m,assessment_ids=list(m.get('assessment_ids',())),
                    test_count=m['module_test_count']+m['skip_test_count']) for mid,m in ms.items()})

    def report_stats(self):
        result=self.stats();qs,_,_=self._project()
        for pid,p in result['points'].items():
            rows=[qs[qid] for qid,q in self._questions.items() if qid in self._final_question_ids and pid in q.get('point_ids',[])]
            p.update(ever_weak=any(q['ever_wrong'] for q in rows),ever_red=any(q['ever_wrong'] for q in rows),
                check_errors_before_pass=sum(q['check_errors_before_pass'] for q in rows),
                check_passed_at=max((q['check_passed_at'] for q in rows if q['check_passed_at']),default=None))
        return result

    def to_dict(self):
        data = dict(version=2,points=[asdict(p) for p in self._points],questions=list(self._questions.values()),taught=sorted(self._taught),
            events=[event_to_dict(e) for e in sorted(self._events.values(),key=lambda e:(e.seq,e.event_id))])
        if self._event_details:data['event_details']=copy.deepcopy(self._event_details)
        if self._final_question_ids != set(self._questions):data['final_question_ids']=sorted(self._final_question_ids)
        if self._historical_event_ids: data['historical_event_ids'] = sorted(self._historical_event_ids)
        return data

    @classmethod
    def from_dict(cls,data):
        try:
            if data['version']!=2:raise ValueError()
            book=cls(data['points'],data['questions'],data['taught'],historical_event_ids=data.get('historical_event_ids',()),
                     final_question_ids=data.get('final_question_ids'))
            details=data.get('event_details',{})
            for event in data['events']:book.apply(event,details=details.get(event['event_id']))
            return book
        except (ValueError,TypeError,KeyError):raise MasteryError('INVALID_DATA') from None


def _historical_progress(refs, events):
    """Replay pre-70 point outcomes for attained progress, not new assessment.

    The prior point-based projection (eb4d40a6) is retained here only for its
    states/pass counts/levels. Original immutable events remain the authority;
    no snapshot table or replacement answers are created by migration.
    """
    if not events: return {}, {}
    points={p.point_id:dict(state='untested',module_errors=0,level=None) for p in refs}
    modules={mid:dict(state='untested',module_test_count=0,skip_test_count=0,retest_count=0,
        first_test=None,skip_test_passed=False) for mid in {p.module_id for p in refs}}
    grouped={mid:[p.point_id for p in refs if p.module_id==mid] for mid in modules}
    touched_points,touched_modules=set(),set()
    attempts,closed={},set()
    for e in sorted(events,key=lambda e:(e.seq,e.event_id)):
        m=modules[e.module_id];pids=grouped[e.module_id];key=(e.module_id,e.attempt_id)
        touched_modules.add(e.module_id)
        if isinstance(e,AttemptClosed):
            answers=attempts.get(key,{})
            mastered={pid for pid,values in answers.items() if values and all(values)}
            passed=len(mastered)*10>=len(pids)*9
            m[e.kind+'_count']+=1
            if m['first_test'] is None:
                m['first_test']=dict(event_id=e.event_id,attempt_id=e.attempt_id,kind=e.kind,
                    mastered_points=len(mastered),total_points=len(pids),mastery_ratio=len(mastered)/len(pids))
            if e.kind=='skip_test' and passed: m['skip_test_passed']=True
            if e.kind=='skip_test' and not passed:
                m['state']='untested'
                for pid in pids: points[pid]['state']='untested'
            else:
                m['state']=('skipped' if e.kind=='skip_test' else 'passed') if passed else 'failed'
                for pid in pids: points[pid]['state']='green' if pid in mastered else 'weak' if passed else 'red'
            touched_points.update(pids);closed.add(key);continue
        touched_points.update(pid for pid,_ in e.results)
        if e.context in ('module_test','skip_test','retest'):
            for pid,correct in e.results: points[pid]['module_errors']+=int(not correct)
        if e.context in ('module_test','skip_test') and key not in closed:
            for pid,correct in e.results: attempts.setdefault(key,{}).setdefault(pid,[]).append(correct)
        elif e.context=='retest':
            m['retest_count']+=1
            for pid,correct in e.results:
                if points[pid]['state']=='red' and correct: points[pid]['state']='green'
            if m['state']=='failed' and all(points[pid]['state']!='red' for pid in pids): m['state']='passed'
        elif e.context=='check' and all(m['state'] in ('passed','skipped') for m in modules.values()):
            for pid,correct in e.results:
                p=points[pid];level=p['level'] if p['level'] is not None else 1 if p['module_errors'] else 2
                p['level']=3 if level==3 else min(3,level+1) if correct else 1
    for p in points.values():
        if p['level'] is None: p['level']=1 if p['module_errors'] else 2
    return ({pid:points[pid] for pid in touched_points},{mid:modules[mid] for mid in touched_modules})
