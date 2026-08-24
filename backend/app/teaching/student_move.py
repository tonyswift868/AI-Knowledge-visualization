"""Deterministic, provider-free resolution of learner input to ``StudentMove``.

The resolver interprets input only.  It deliberately has no persistence,
grading, mastery, task, or planning dependencies.  Product actions are trusted
ahead of text heuristics, while ambiguous text remains visibly uncertain.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .contracts import CurrentTaskKind, StudentMove, TeacherMove


class ResolutionSource(str, Enum):
    EXPLICIT_ACTION = "explicit_action"
    RULE = "rule"
    FALLBACK = "fallback"
    CLASSIFIER = "classifier"


@dataclass(frozen=True, slots=True)
class StudentMoveContext:
    """Read-only teaching context that may disambiguate a learner utterance."""

    current_concept: str = ""
    previous_teacher_move: TeacherMove | str | None = None
    current_task: CurrentTaskKind | str = CurrentTaskKind.NONE


@dataclass(frozen=True, slots=True)
class StudentMoveResolution:
    """Explainable output consumed by a future ``LearnerContext`` builder."""

    primary_move: StudentMove
    confidence: float
    alternatives: tuple[StudentMove, ...] = ()
    ambiguous: bool = False
    reason: str = ""
    source: ResolutionSource = ResolutionSource.RULE

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.reason:
            raise ValueError("reason must explain the resolution")
        if self.primary_move in self.alternatives:
            raise ValueError("alternatives cannot repeat the primary move")
        if len(set(self.alternatives)) != len(self.alternatives):
            raise ValueError("alternatives must be unique")
        if self.alternatives and not self.ambiguous:
            raise ValueError("alternatives require ambiguous=true")

    @property
    def move(self) -> StudentMove:
        """Compatibility shorthand for consumers that only need the primary move."""

        return self.primary_move


class StudentMoveClassifierHook(Protocol):
    """Optional future classifier boundary; the rule path never calls it by default."""

    def __call__(
        self,
        text: str,
        context: StudentMoveContext,
    ) -> StudentMoveResolution | None: ...


@dataclass(frozen=True, slots=True)
class _TextRule:
    move: StudentMove
    confidence: float
    reason: str
    patterns: tuple[re.Pattern[str], ...]

    def matches(self, text: str) -> bool:
        return any(pattern.search(text) is not None for pattern in self.patterns)


def _text_rule(
    move: StudentMove,
    confidence: float,
    reason: str,
    *patterns: str,
) -> _TextRule:
    return _TextRule(
        move=move,
        confidence=confidence,
        reason=reason,
        patterns=tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns),
    )


_TEXT_RULES = (
    _text_rule(
        StudentMove.RELATED_TANGENT,
        0.99,
        "text attempts to control protected learning state or instructions",
        r"(?:忽略|绕过).{0,12}(?:之前|上面|系统).{0,8}(?:指令|规则)",
        r"(?:修改|设置|标记).{0,12}(?:掌握|学习进度|任务|计划|分数|成绩)",
        r"(?:system prompt|developer message|系统提示词)",
    ),
    _text_rule(
        StudentMove.REQUEST_ASSESSMENT,
        0.99,
        "text explicitly requests an assessment",
        r"(?:考考?我|测试(?:一下)?我|测测我|给我出(?:一道|几道)?题|来(?:一道|几道)?题|quiz me)",
    ),
    _text_rule(
        StudentMove.LEARNER_SAYS_MASTERED,
        0.98,
        "learner explicitly self-attests mastery",
        r"(?:^|[，,。.!！?？\s])(?:这个|这部分|这一块)?\s*(?:我)?(?:已经)?(?:会了|懂了|掌握了|学会了|明白了)",
    ),
    _text_rule(
        StudentMove.REQUEST_SKIP,
        0.98,
        "text explicitly requests moving past the current concept",
        r"(?:下一个|跳过(?:这个)?|先不(?:讲|学)(?:这个)?|换下一(?:个|节)|next topic)",
    ),
    _text_rule(
        StudentMove.REQUEST_HINT,
        0.97,
        "text explicitly requests a hint",
        r"(?:提示(?:一下|点)?|给(?:我)?(?:点|个)提示|来点提示|给点线索|hint)",
    ),
    _text_rule(
        StudentMove.REQUEST_CONNECTION,
        0.97,
        "text asks how this relates to another concept",
        r"(?:跟|和|与).{0,16}(?:什么关系|有啥关系|有什么关系|怎么关联)",
        r"(?:联系|关联).{0,8}(?:是什么|在哪|起来)",
    ),
    _text_rule(
        StudentMove.ASK_WHY,
        0.97,
        "text asks for a reason or cause",
        r"(?:为什么|为何|为啥|怎么会|原因(?:是)?什么|why\b)",
    ),
    _text_rule(
        StudentMove.REQUEST_SIMPLIFY,
        0.96,
        "text requests a simpler explanation",
        r"(?:讲|说|解释).{0,5}(?:简单|通俗)",
        r"(?:简单|通俗).{0,5}(?:点|一点|讲|解释)",
        r"(?:太难了|太复杂了)",
    ),
    _text_rule(
        StudentMove.ASK_EXAMPLE,
        0.96,
        "text explicitly requests an example",
        r"(?:换|举|来|给).{0,5}(?:个|一个|些)?例子",
        r"(?:例子|示例).{0,5}(?:呢|吗|看看|说明)",
    ),
    _text_rule(
        StudentMove.ASK_DEFINITION,
        0.96,
        "text asks for a definition",
        r"(?:是什么|什么意思|定义(?:是什么|呢)?|何谓|怎么理解)",
    ),
    _text_rule(
        StudentMove.REQUEST_PRACTICAL_USE,
        0.95,
        "text requests a practical application",
        r"(?:实际|实战|工程|代码|项目).{0,12}(?:怎么用|如何用|应用|落地)",
        r"(?:有什么用|有啥用|应用场景)",
    ),
    _text_rule(
        StudentMove.CHALLENGE_TEACHER,
        0.95,
        "learner challenges the teacher's claim",
        r"(?:你说的?|这个(?:说法)?).{0,10}(?:不对|有问题|错)",
        r"(?:不对吧|我不认同|你确定吗|真的吗)",
    ),
    _text_rule(
        StudentMove.CONFUSED,
        0.94,
        "learner explicitly signals confusion",
        r"(?:没|不|未)(?:有)?(?:听|看)?懂",
        r"(?:不明白|搞不懂|没理解|懵了?|跟不上)",
    ),
    _text_rule(
        StudentMove.ASK_DEEPER_EXPLANATION,
        0.93,
        "text requests a deeper explanation",
        r"(?:详细|深入|展开).{0,8}(?:讲|解释|说|一点)?",
        r"(?:底层|深层).{0,5}(?:原理|机制)",
        r"(?:再深一点|讲深一点)",
    ),
    _text_rule(
        StudentMove.RELATED_TANGENT,
        0.90,
        "learner explicitly opens a related side question",
        r"(?:题外话|顺便问|另外(?:问|一个问题)|换个话题|先不说这个|说到别的)",
    ),
    _text_rule(
        StudentMove.CONTINUE,
        0.92,
        "text explicitly asks to continue",
        r"^(?:继续|接着(?:讲|说)?|往下(?:讲|说)?|好的?|可以|嗯+|ok(?:ay)?)[。.!！?？,，]*$",
    ),
)


_EXPLICIT_ACTION_ALIASES = {
    "mastered": StudentMove.LEARNER_SAYS_MASTERED,
    "mark_mastered": StudentMove.LEARNER_SAYS_MASTERED,
    "i_mastered": StudentMove.LEARNER_SAYS_MASTERED,
    "我已掌握": StudentMove.LEARNER_SAYS_MASTERED,
    "我会了": StudentMove.LEARNER_SAYS_MASTERED,
    "assessment": StudentMove.REQUEST_ASSESSMENT,
    "assess_me": StudentMove.REQUEST_ASSESSMENT,
    "quiz_me": StudentMove.REQUEST_ASSESSMENT,
    "考我": StudentMove.REQUEST_ASSESSMENT,
    "skip": StudentMove.REQUEST_SKIP,
    "next": StudentMove.REQUEST_SKIP,
    "下一个": StudentMove.REQUEST_SKIP,
    "hint": StudentMove.REQUEST_HINT,
    "提示": StudentMove.REQUEST_HINT,
    "answer_correct": StudentMove.CORRECT_ANSWER_SIGNAL,
    "correct_answer": StudentMove.CORRECT_ANSWER_SIGNAL,
    "answer_incorrect": StudentMove.INCORRECT_ANSWER_SIGNAL,
    "incorrect_answer": StudentMove.INCORRECT_ANSWER_SIGNAL,
    "answer_partial": StudentMove.PARTIAL_ANSWER_SIGNAL,
    "partial_answer": StudentMove.PARTIAL_ANSWER_SIGNAL,
}

_ANSWER_ATTEMPT_PATTERN = re.compile(
    r"(?:^|[，,\s])(?:答案|结果|我选|我认为|我觉得|应该)(?:是|为|选)?|^[a-d]$|^-?\d+(?:\.\d+)?$",
    re.IGNORECASE,
)
_ANSWER_CONTEXT_MOVES = frozenset(
    {
        TeacherMove.HINT,
        TeacherMove.DIAGNOSE,
        TeacherMove.GUIDED_PRACTICE,
        TeacherMove.RETRIEVAL_PROMPT,
        TeacherMove.TRANSFER_CHALLENGE,
    }
)
_ANSWER_CONTEXT_TASKS = frozenset(
    {CurrentTaskKind.PRACTICE, CurrentTaskKind.ASSESSMENT, CurrentTaskKind.REVIEW}
)


def _normalize_text(text: str | None) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TypeError("text must be a string or None")
    normalized = unicodedata.normalize("NFKC", text).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _normalize_action(action: StudentMove | str | None) -> StudentMove | None:
    if action is None or isinstance(action, StudentMove):
        return action
    if not isinstance(action, str):
        raise TypeError("explicit_action must be a StudentMove, string, or None")
    normalized = unicodedata.normalize("NFKC", action).strip().casefold()
    normalized = re.sub(r"[\s-]+", "_", normalized)
    if not normalized:
        return None
    if normalized in _EXPLICIT_ACTION_ALIASES:
        return _EXPLICIT_ACTION_ALIASES[normalized]
    try:
        return StudentMove(normalized)
    except ValueError:
        try:
            return StudentMove[normalized.upper()]
        except KeyError:
            return None


def _coerce_teacher_move(value: TeacherMove | str | None) -> TeacherMove | None:
    if value is None or isinstance(value, TeacherMove):
        return value
    try:
        return TeacherMove(value)
    except ValueError:
        return None


def _coerce_task(value: CurrentTaskKind | str) -> CurrentTaskKind:
    if isinstance(value, CurrentTaskKind):
        return value
    try:
        return CurrentTaskKind(value)
    except ValueError:
        return CurrentTaskKind.NONE


def _looks_like_answer(text: str, context: StudentMoveContext) -> bool:
    if _ANSWER_ATTEMPT_PATTERN.search(text) is not None:
        return True
    teacher_move = _coerce_teacher_move(context.previous_teacher_move)
    task = _coerce_task(context.current_task)
    return (
        len(text) <= 160
        and "?" not in text
        and "？" not in text
        and (teacher_move in _ANSWER_CONTEXT_MOVES or task in _ANSWER_CONTEXT_TASKS)
    )


def _rule_resolution(text: str, context: StudentMoveContext) -> StudentMoveResolution:
    if not text:
        return StudentMoveResolution(
            primary_move=StudentMove.CONTINUE,
            confidence=0.0,
            ambiguous=True,
            reason="blank input contains no classifiable learner signal",
            source=ResolutionSource.FALLBACK,
        )

    matches = [rule for rule in _TEXT_RULES if rule.matches(text)]
    if _looks_like_answer(text, context):
        matches.append(
            _TextRule(
                move=StudentMove.ANSWER_ATTEMPT,
                confidence=0.88,
                reason="text is an answer attempt without a correctness judgment",
                patterns=(),
            )
        )

    best_by_move: dict[StudentMove, _TextRule] = {}
    for rule in matches:
        previous = best_by_move.get(rule.move)
        if previous is None or rule.confidence > previous.confidence:
            best_by_move[rule.move] = rule

    ranked = sorted(best_by_move.values(), key=lambda rule: rule.confidence, reverse=True)
    if not ranked:
        return StudentMoveResolution(
            primary_move=StudentMove.CONTINUE,
            confidence=0.35,
            alternatives=(StudentMove.ANSWER_ATTEMPT, StudentMove.RELATED_TANGENT),
            ambiguous=True,
            reason="no reliable deterministic signal matched the text",
            source=ResolutionSource.FALLBACK,
        )

    primary = ranked[0]
    alternatives = tuple(rule.move for rule in ranked[1:])
    ambiguous = bool(alternatives)
    confidence = min(primary.confidence, 0.78) if ambiguous else primary.confidence
    reason = primary.reason
    if primary.move is StudentMove.ASK_WHY and (
        context.current_concept.strip() or context.previous_teacher_move is not None
    ):
        reason += "; previous teaching context identifies the question target"
    if alternatives:
        reason += "; additional signals: " + ", ".join(move.value for move in alternatives)
    return StudentMoveResolution(
        primary_move=primary.move,
        confidence=confidence,
        alternatives=alternatives,
        ambiguous=ambiguous,
        reason=reason,
        source=ResolutionSource.RULE,
    )


class StudentMoveResolver:
    """Small rule-first resolver with an explicitly opt-in classifier seam."""

    def __init__(self, classifier_hook: StudentMoveClassifierHook | None = None) -> None:
        self._classifier_hook = classifier_hook
        self._classifier_calls = 0

    @property
    def classifier_calls(self) -> int:
        return self._classifier_calls

    def resolve(
        self,
        text: str | None,
        *,
        explicit_action: StudentMove | str | None = None,
        context: StudentMoveContext | None = None,
        use_classifier: bool = False,
    ) -> StudentMoveResolution:
        normalized_action = _normalize_action(explicit_action)
        if normalized_action is not None:
            return StudentMoveResolution(
                primary_move=normalized_action,
                confidence=1.0,
                reason=f"recognized explicit product action: {normalized_action.value}",
                source=ResolutionSource.EXPLICIT_ACTION,
            )

        raw_text = "" if text is None else text
        normalized_text = _normalize_text(text)
        resolution = _rule_resolution(normalized_text, context or StudentMoveContext())
        if use_classifier and resolution.ambiguous and self._classifier_hook is not None:
            self._classifier_calls += 1
            classified = self._classifier_hook(raw_text, context or StudentMoveContext())
            if classified is not None:
                return classified
        return resolution


def resolve_student_move(
    text: str | None,
    *,
    explicit_action: StudentMove | str | None = None,
    context: StudentMoveContext | None = None,
    classifier_hook: StudentMoveClassifierHook | None = None,
    use_classifier: bool = False,
) -> StudentMoveResolution:
    """Resolve one learner input without changing any learning state."""

    return StudentMoveResolver(classifier_hook).resolve(
        text,
        explicit_action=explicit_action,
        context=context,
        use_classifier=use_classifier,
    )


__all__ = [
    "ResolutionSource",
    "StudentMoveClassifierHook",
    "StudentMoveContext",
    "StudentMoveResolution",
    "StudentMoveResolver",
    "resolve_student_move",
]
