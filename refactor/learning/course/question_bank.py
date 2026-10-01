"""Bounded, model-free selection from a plan's synchronized question bank."""
from __future__ import annotations

import random
import hashlib
import json


def initial_deficits(questions, point_ids):
    """Two singletons in total, including already-correct questions."""
    return {pid: max(0, 2 - sum(q['point_ids'] == [pid] for q in questions)) for pid in point_ids}


def final_deficits(questions, levels, *, asked=()):
    """Only reserve what remains: one fresh question at level 2, two at 0/1."""
    asked = set(asked)
    return {pid: max(0, (0 if level == 3 else 1 if level == 2 else 2) - sum(
        q['point_ids'] == [pid] and not q.get('correct_seen') and q['id'] not in asked for q in questions))
        for pid, level in levels.items()}


def answer_history(questions, answers, judgements=()):
    """Asked IDs and per-point angles; saved pre-v5 judgements match by stem."""
    asked, angles = set(), {}
    for q in questions:
        if q.get('asked_in'):
            asked.add(q['id'])
            for pid in q['point_ids']:
                if q.get('angle'): angles.setdefault(pid, set()).add(q['angle'])
    by_stem = {q['stem']: q for q in questions}
    for row in answers:
        if row.get('question_id'): asked.add(row['question_id'])
        for pid in row['point_ids']:
            if row.get('angle'): angles.setdefault(pid, set()).add(row['angle'])
    for row in judgements:
        q = by_stem.get((row.get('details') or {}).get('question'))
        if q:
            asked.add(q['id'])
            for pid in q['point_ids']:
                if q.get('angle'): angles.setdefault(pid, set()).add(q['angle'])
    return asked, angles


def material_fingerprint(points, excerpts):
    """Only teaching content changes invalidate questions, never schedule/order."""
    content = [{'id': p['id'], 'title': p['title'], 'summary': p.get('summary', ''),
                'chunk_ids': p['chunk_ids']} for p in points]
    raw = json.dumps([content, excerpts], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


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
