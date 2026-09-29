"""Deterministic mastery projection from immutable, ID-idempotent events.

Equal sequence numbers use event_id as a deterministic tie-breaker. Arriving
early checks/retests are retained, but only affect levels/red points once their
prerequisites exist earlier in the replay. One multi-point check is one question.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import copy

ANGLES = ('recall', 'apply', 'contrast', 'fix_error', 'transfer')
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
        if self.context == 'check' and self.angle not in ANGLES:
            raise MasteryError('INVALID_ANGLE')
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
        if self.kind not in ('module_test', 'skip_test'):
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
class PointState:
    point_id: str
    module_id: str
    state: str
    casual_errors: int
    module_errors: int
    level: int
    check_questions: int
    check_correct: int
    last_question: int | None
    used_angles: tuple[str, ...]


@dataclass(frozen=True)
class ModuleState:
    module_id: str
    state: str
    point_ids: tuple[str, ...]
    module_test_count: int
    skip_test_count: int


class MasteryBook:
    def __init__(self, points):
        self._points = tuple(p if isinstance(p, PointRef) else PointRef(**p) for p in points)
        self._refs = {p.point_id: p for p in self._points}
        if len(self._refs) != len(self._points):
            raise MasteryError('DUPLICATE_POINT')
        self._events = {}
        self._modules = {p.module_id for p in self._points}
        self._attempt_kinds = {}
        self._closed_attempts = set()
        self._cache = None

    def apply(self, event):
        if isinstance(event, dict):
            event = event_from_dict(event)
        if not isinstance(event, (Judgement, AttemptClosed)):
            raise MasteryError('INVALID_EVENT')
        previous = self._events.get(event.event_id)
        if previous is not None:
            if event_to_dict(previous) != event_to_dict(event):
                raise MasteryError('EVENT_CONFLICT', '同一事件标识不能包含不同内容。')
            return
        if event.module_id not in self._modules:
            raise MasteryError('UNKNOWN_MODULE')
        if isinstance(event, Judgement):
            for pid, _ in event.results:
                if pid not in self._refs or self._refs[pid].module_id != event.module_id:
                    raise MasteryError('POINT_MODULE_MISMATCH')
        attempt = event.attempt_id
        kind = event.kind if isinstance(event, AttemptClosed) else event.context
        if attempt and kind in ('module_test', 'skip_test'):
            key = (event.module_id, attempt)
            if self._attempt_kinds.get(key, kind) != kind:
                raise MasteryError('ATTEMPT_CONFLICT')
            if isinstance(event, AttemptClosed) and key in self._closed_attempts:
                raise MasteryError('ATTEMPT_ALREADY_CLOSED')
            self._attempt_kinds[key] = kind
            if isinstance(event, AttemptClosed): self._closed_attempts.add(key)
        self._events[event.event_id] = event
        self._cache = None

    def attempt_closed(self, module_id, attempt_id):
        return (module_id, attempt_id) in self._closed_attempts

    def _project(self):
        if self._cache is not None:
            return self._cache
        points = {p.point_id: dict(state='untested', casual_errors=0, module_errors=0,
                                  level=None, check_questions=0, check_correct=0,
                                  last_question=None, angles={}, ever_weak=False, ever_red=False,
                                  check_errors_before_pass=0, check_passed_at=None) for p in self._points}
        modules = {}
        for ref in self._points:
            modules.setdefault(ref.module_id, dict(state='untested', point_ids=[], module_test_count=0, skip_test_count=0,
                                                   first_test=None, retest_count=0, skip_test_passed=False))['point_ids'].append(ref.point_id)
        attempts, closed = {}, set()
        question = 0
        for event in sorted(self._events.values(), key=lambda e: (e.seq, e.event_id)):
            module = modules[event.module_id]
            if isinstance(event, AttemptClosed):
                key = (event.module_id, event.attempt_id)
                answers = attempts.get(key, {})
                mastered = {pid for pid, values in answers.items() if values and all(values)}
                passed = len(mastered) * 10 >= len(module['point_ids']) * 9
                module[event.kind + '_count'] += 1
                if module['first_test'] is None:
                    module['first_test'] = dict(event_id=event.event_id, attempt_id=event.attempt_id, kind=event.kind,
                                                mastered_points=len(mastered), total_points=len(module['point_ids']),
                                                mastery_ratio=len(mastered) / len(module['point_ids']))
                if event.kind == 'skip_test' and passed:
                    module['skip_test_passed'] = True
                if event.kind == 'skip_test' and not passed:
                    module['state'] = 'untested'
                    for pid in module['point_ids']:
                        points[pid]['state'] = 'untested'
                else:
                    module['state'] = ('skipped' if event.kind == 'skip_test' else 'passed') if passed else 'failed'
                    for pid in module['point_ids']:
                        points[pid]['state'] = 'green' if pid in mastered else ('weak' if passed else 'red')
                        if points[pid]['state'] in ('weak', 'red'):
                            points[pid]['ever_' + points[pid]['state']] = True
                closed.add(key)
                continue
            if event.context in ('casual', 'quick_confirm', 'module_test', 'retest', 'skip_test'):
                for pid, correct in event.results:
                    if not correct:
                        category = 'casual_errors' if event.context in ('casual', 'quick_confirm') else 'module_errors'
                        points[pid][category] += 1
            if event.context in ('module_test', 'skip_test'):
                key = (event.module_id, event.attempt_id)
                if key not in closed:
                    answers = attempts.setdefault(key, {})
                    for pid, correct in event.results:
                        answers.setdefault(pid, []).append(correct)
            elif event.context == 'retest':
                module['retest_count'] += 1  # One judgement is one question, including multi-point questions.
                for pid, correct in event.results:
                    if points[pid]['state'] == 'red' and correct:
                        points[pid]['state'] = 'green'
                if module['state'] == 'failed' and all(points[pid]['state'] != 'red' for pid in module['point_ids']):
                    module['state'] = 'passed'
            elif event.context == 'check' and all(m['state'] in ('passed', 'skipped') for m in modules.values()):
                question += 1
                for pid, correct in event.results:
                    point = points[pid]
                    level = self._level(point)
                    if level < 3 and not correct:
                        point['check_errors_before_pass'] += 1
                    point['level'] = 3 if level == 3 else min(3, level + 1) if correct else 1
                    if level < 3 and point['level'] == 3:
                        point['check_passed_at'] = event.at.isoformat()
                    point['check_questions'] += 1
                    point['check_correct'] += int(correct)
                    point['last_question'] = question
                    point['angles'][event.angle] = question
        self._cache = points, modules, question
        return self._cache

    @staticmethod
    def _level(point):
        return point['level'] if point['level'] is not None else (1 if point['module_errors'] else 2)

    def point(self, point_id) -> PointState:
        points, _, _ = self._project()
        if point_id not in points:
            raise MasteryError('UNKNOWN_POINT')
        p = points[point_id]
        return PointState(point_id, self._refs[point_id].module_id, p['state'], p['casual_errors'], p['module_errors'],
                          self._level(p), p['check_questions'], p['check_correct'], p['last_question'], tuple(p['angles']))

    def module(self, module_id) -> ModuleState:
        _, modules, _ = self._project()
        if module_id not in modules:
            raise MasteryError('UNKNOWN_MODULE')
        m = modules[module_id]
        return ModuleState(module_id, m['state'], tuple(m['point_ids']), m['module_test_count'], m['skip_test_count'])

    def check_ready(self) -> bool:
        _, modules, _ = self._project()
        return bool(modules) and all(m['state'] in ('passed', 'skipped') for m in modules.values())

    def check_complete(self) -> bool:
        points, _, _ = self._project()
        return self.check_ready() and all(self._level(p) == 3 for p in points.values())

    def next_check_items(self, n=1, min_gap=3):
        if type(n) is not int or n < 0 or type(min_gap) is not int or min_gap < 0:
            raise MasteryError('INVALID_QUERY')
        if not self.check_ready():
            return []
        points, _, question = self._project()
        points = copy.deepcopy(points)  # Querying reserves nothing and mutates no events.
        order = {p.point_id: i for i, p in enumerate(self._points)}
        candidates = {pid for pid, p in points.items() if self._level(p) < 3}
        result = []
        while candidates and len(result) < n:
            def priority(pid):
                p = points[pid]
                errors = p['casual_errors'] + p['module_errors']
                if p['last_question'] is None:
                    return (0, not (errors or p['state'] == 'weak'), self._level(p), -errors, order[pid])
                return (1, self._level(p), -errors, p['last_question'], order[pid])
            eligible = [pid for pid in candidates if points[pid]['last_question'] is None
                        or question - points[pid]['last_question'] >= min_gap]
            # If every remaining candidate is gap-blocked, relax the gap to
            # avoid deadlock in a small set. Returned batch items are distinct.
            pid = min(eligible or candidates, key=priority)
            used = points[pid]['angles']
            angle = next((a for a in ANGLES if a not in used), None)
            if angle is None:
                angle = min(ANGLES, key=lambda a: used[a])
            result.append((pid, angle)); candidates.remove(pid)
            question += 1
        return result

    def stats(self):
        points, modules, questions = self._project()
        return dict(check_questions=questions, points={pid: dict(
            casual_errors=p['casual_errors'], module_errors=p['module_errors'],
            check_questions=p['check_questions'], check_correct=p['check_correct'],
            check_accuracy=p['check_correct'] / p['check_questions'] if p['check_questions'] else 0.0,
        ) for pid, p in points.items()}, modules={mid: dict(
            module_test_count=m['module_test_count'], skip_test_count=m['skip_test_count'],
            test_count=m['module_test_count'] + m['skip_test_count']) for mid, m in modules.items()})

    def report_stats(self):
        """Historical evidence, computed in the same replay as mastery.

        The first *closed* module/skip test counts unasked points as unmastered.
        Retests count judgement events, not point results or optional attempt IDs.
        Pre-pass errors ignore ineligible checks and checks after terminal level 3.
        Keeping this separate preserves the existing stats() response contract.
        """
        points, modules, _ = self._project()
        result = self.stats()
        for pid, point in points.items():
            result['points'][pid].update({key: point[key] for key in
                ('ever_weak', 'ever_red', 'check_errors_before_pass', 'check_passed_at')})
        for mid, module in modules.items():
            result['modules'][mid].update({key: copy.deepcopy(module[key]) for key in
                ('first_test', 'retest_count', 'skip_test_passed')})
        return result

    def to_dict(self):
        return dict(version=1, points=[asdict(p) for p in self._points],
                    events=[event_to_dict(e) for e in sorted(self._events.values(), key=lambda e: (e.seq, e.event_id))])

    @classmethod
    def from_dict(cls, data):
        try:
            if set(data) != {'version', 'points', 'events'} or type(data['version']) is not int or data['version'] != 1:
                raise ValueError()
            book = cls(PointRef(**p) for p in data['points'])
            for event in data['events']:
                book.apply(event_from_dict(event))
            return book
        except (TypeError, ValueError, KeyError):
            raise MasteryError('INVALID_DATA', '掌握度账本数据无效。') from None
