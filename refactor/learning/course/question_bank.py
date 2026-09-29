"""Bounded, model-free selection from a plan's synchronized question bank."""
from __future__ import annotations

import random


def choose_questions(questions, targets, unstable=(), *, limit=None, rng=None, smaller_than=None,
                     single_once=(), asked=(), used_angles=None):
    """Cover each target with few questions; unstable points get single-point questions.

    A wrong composite is handled by calling this again with ``smaller_than``.
    Selection never mutates the bank or mastery. The caller records correct-seen
    only after an answer and may refill the bank if coverage is incomplete.
    """
    rng = rng or random.Random()
    targets = tuple(dict.fromkeys(targets))
    unstable = set(unstable)
    single_once = set(single_once)
    asked = set(asked)
    cap = max(1, 2 * len(targets)) if limit is None else max(0, limit)
    pool = [q for q in questions if not q.get('correct_seen') and q['id'] not in asked
            and set(q['point_ids']) <= set(targets)
            and (smaller_than is None or len(q['point_ids']) < smaller_than)
            and (used_angles is None or all(q['angle'] not in used_angles.get(pid, ()) for pid in q['point_ids']))
            and (len(q['point_ids']) != 1 or q['point_ids'][0] not in single_once)]
    selected, uncovered = [], set(targets)
    while uncovered and len(selected) < cap:
        eligible = [q for q in pool if set(q['point_ids']) & uncovered and
                    (len(q['point_ids']) == 1 or not set(q['point_ids']) & unstable)]
        if not eligible:
            break
        # Unstable points first. Stable points prefer a larger non-overlapping
        # combination. Randomness only breaks equally ranked candidates.
        best = max((int(len(q['point_ids']) == 1 and q['point_ids'][0] in unstable),
                    len(set(q['point_ids']) & uncovered), -len(set(q['point_ids']) - uncovered)) for q in eligible)
        tied = [q for q in eligible if (int(len(q['point_ids']) == 1 and q['point_ids'][0] in unstable),
                    len(set(q['point_ids']) & uncovered), -len(set(q['point_ids']) - uncovered)) == best]
        picked = rng.choice(tied)
        selected.append(picked)
        uncovered.difference_update(picked['point_ids'])
        pool.remove(picked)
    return selected, tuple(pid for pid in targets if pid in uncovered)


def review_points(practice_results):
    """Latest free-practice outcome per point; no mastery or report mutation."""
    latest = {}
    for row in sorted(practice_results, key=lambda r: (r['at'], r.get('sequence', 0), r['id'])):
        for pid in row['point_ids']:
            latest[pid] = not row['correct']
    return {pid for pid, needs_review in latest.items() if needs_review}
