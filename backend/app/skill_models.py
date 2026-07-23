"""Explainable skill estimates kept separate from memory scheduling."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable


def _probability(value: float) -> float:
    return round(max(0.0001, min(0.9999, float(value))), 6)


@dataclass(frozen=True)
class BKTParameters:
    prior: float = 0.2
    learn: float = 0.15
    guess: float = 0.2
    slip: float = 0.1

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not 0 <= value < 1:
                raise ValueError(f"{name} must be in [0, 1)")
        if self.guess + self.slip >= 1:
            raise ValueError("guess + slip must be less than 1")


@dataclass(frozen=True)
class BKTState:
    skill_id: str
    probability_known: float
    observations: int = 0
    correct_observations: int = 0
    last_explanation: dict[str, Any] | None = None


def initial_bkt(skill_id: str, parameters: BKTParameters | None = None) -> BKTState:
    if not skill_id.strip():
        raise ValueError("skill_id is required")
    params = parameters or BKTParameters()
    return BKTState(skill_id=skill_id, probability_known=_probability(params.prior))


def update_bkt(
    state: BKTState,
    *,
    correct: bool,
    parameters: BKTParameters | None = None,
) -> BKTState:
    """One standard BKT observation update with an auditable explanation."""
    params = parameters or BKTParameters()
    prior = _probability(state.probability_known)
    if correct:
        likelihood_known = prior * (1 - params.slip)
        likelihood_unknown = (1 - prior) * params.guess
    else:
        likelihood_known = prior * params.slip
        likelihood_unknown = (1 - prior) * (1 - params.guess)
    denominator = likelihood_known + likelihood_unknown
    posterior = likelihood_known / denominator if denominator else prior
    after_learning = posterior + (1 - posterior) * params.learn
    explanation = {
        "model": "bkt-v1",
        "skill_id": state.skill_id,
        "observation": "correct" if correct else "incorrect",
        "prior": round(prior, 6),
        "posterior_before_learning": round(posterior, 6),
        "learn_transition": params.learn,
        "guess": params.guess,
        "slip": params.slip,
        "probability_known": _probability(after_learning),
    }
    return replace(
        state,
        probability_known=_probability(after_learning),
        observations=state.observations + 1,
        correct_observations=state.correct_observations + int(correct),
        last_explanation=explanation,
    )


@dataclass(frozen=True)
class RuleV1State:
    """Current deterministic baseline, intentionally simpler than BKT."""

    skill_id: str
    mastery: float = 0.0
    attempts: int = 0
    independent_passes: int = 0
    hint_uses: int = 0


def update_rule_v1(
    state: RuleV1State,
    *,
    score: float,
    independent: bool = True,
    used_hint: bool = False,
) -> RuleV1State:
    score = max(0.0, min(1.0, float(score)))
    evidence = score * (1.0 if independent else 0.72) - (0.08 if used_hint else 0.0)
    mastery = state.mastery * 0.75 + max(0.0, evidence) * 0.25
    return replace(
        state,
        mastery=round(max(0.0, min(1.0, mastery)), 6),
        attempts=state.attempts + 1,
        independent_passes=state.independent_passes + int(independent and score >= 0.75),
        hint_uses=state.hint_uses + int(used_hint),
    )


def compare_skill_models(
    skill_id: str,
    observations: Iterable[dict[str, Any]],
    *,
    parameters: BKTParameters | None = None,
) -> dict[str, Any]:
    """Replay identical evidence through BKT and Rule V1 without fusing them."""
    bkt = initial_bkt(skill_id, parameters)
    rule = RuleV1State(skill_id=skill_id)
    for item in observations:
        score = float(item.get("score", 1.0 if item.get("correct") else 0.0))
        correct = bool(item.get("correct", score >= 0.75))
        bkt = update_bkt(bkt, correct=correct, parameters=parameters)
        rule = update_rule_v1(
            rule,
            score=score,
            independent=bool(item.get("independent", True)),
            used_hint=bool(item.get("used_hint", False)),
        )
    return {
        "skill_id": skill_id,
        "bkt": asdict(bkt),
        "rule_v1": asdict(rule),
        "fusion": None,
        "decision_note": "FSRS is excluded: it schedules memory reviews, not skill mastery.",
    }
