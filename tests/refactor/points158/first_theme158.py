"""Explicit offline acceptance of the literal first regrouped theme.

Default: no action. --run uses only the existing D: task backup and a fake
teacher, with the proposed stable-ID adapter explicitly labelled in evidence.
"""
from copy import deepcopy
from datetime import date, datetime, timezone
import argparse
import json
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from refactor.learning.app.classroom import Classroom
from refactor.learning.course.restructure import apply_structure
from refactor.learning.course.store import CourseStore
from tests.refactor.points158.proposed_contract158 import allow_stable_subpoint_ids
from tests.refactor.points158.test_snapshot_apply158 import (
    ACCOUNT_ID, PLAN_ID, _FakeGateway, _NoopMaterials,
)


def verify_first_theme(root):
    root = Path(root).resolve()
    assert root == Path('D:/MentorFlow/.local/test-tmp/points158').resolve()
    copied_root = root / 'first-theme-copy-current'
    source = root / 'data-snapshot' / 'learning/accounts' / ACCOUNT_ID / 'course.sqlite3'
    destination = copied_root / 'learning/accounts' / ACCOUNT_ID / 'course.sqlite3'
    if destination.exists():
        raise FileExistsError('已有测试副本，未覆盖；应先按任务规则清理自己的副本')
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_db = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    target_db = sqlite3.connect(destination)
    try:
        source_db.backup(target_db)
    finally:
        source_db.close()
        target_db.close()
    structure = json.loads((root / 'apply-ready.json').read_text(encoding='utf-8'))
    structure['today'] = '2026-10-17'
    with CourseStore(ACCOUNT_ID, copied_root) as store, allow_stable_subpoint_ids(store):
        apply_structure(store, PLAN_ID, structure, dry_run=False)
        mid = structure['modules'][0]['module_id']
        proposed = {p['id'] for p in structure['modules'][0]['points']}
        ordered = [p for p in store.records('points', PLAN_ID) if p['module_id'] == mid]
        first = next(p for p in ordered if p['id'] in proposed)
        assert first['title'] == '分布式系统基础与典型特征'
        questions = [q for q in store.records('question_bank', PLAN_ID) if first['id'] in q['point_ids']]
        assert len(questions) == 4 and all(q.get('role') == 'retired' for q in questions)
        module = store._get('modules', PLAN_ID, mid)
        module['material_preparation'] = dict(module.get('material_preparation') or {}, state='done')
        module.pop('question_count', None)  # Explicitly skip real preparation in this test copy.
        with store.transaction():
            store._put('modules', PLAN_ID, module)
        scene = deepcopy(store.classroom_position(PLAN_ID)['state'])
        scene.update(module_id=mid, index=ordered.index(first), phase='learning', step='teach',
                     pending=None, test=None, check=None, in_session=True, day='2026-10-17')
        scene.pop('concept', None)
        scene.pop('conversation', None)
        store.save_classroom_position(PLAN_ID, scene)
        fake = _FakeGateway()
        settings = SimpleNamespace(load=lambda: dict(teacher_style='warm', detail='brief', examples='few',
                                                     analogy_domains=['life'], refresh_time='04:00'))
        services = SimpleNamespace(store=store, gateway=fake, account_id=ACCOUNT_ID,
                                   data_root=copied_root, settings=settings)
        room = Classroom(services, lambda: date(2026, 10, 17),
                         now=lambda: datetime(2026, 10, 17, 12, tzinfo=timezone.utc))
        ctx = SimpleNamespace(cancel=threading.Event(), stream=lambda _text: None)
        with patch.object(Classroom, '_materials', staticmethod(lambda: _NoopMaterials())):
            for _ in range(len(first['subpoints']) + 1):
                reply = room.perform(PLAN_ID, 'next', ctx, (), uuid.uuid4().hex)
                assert not reply.get('failure'), reply
        requests = json.dumps(fake.calls, ensure_ascii=False)
        assert all(s['id'] in requests for s in first['subpoints'])
        assert first['subpoints'][0]['notes'][0]['text'] in requests
        credit = store._get('credits', PLAN_ID, 'learn:' + first['id'])
        assert credit['credit']['minutes'] == first['minutes'] == 31
        assert credit['credit']['day'] == '2026-10-17'
        assert store.classroom_position(PLAN_ID)['state']['index'] == ordered.index(first) + 1
        evidence = dict(label='CONDITIONAL STABLE-ID ADAPTER; fake teacher and preparation; no real model',
                        title=first['title'], subpoints=len(first['subpoints']), minutes=first['minutes'],
                        original_questions=4, retired_questions_preserved=4, active_questions=0,
                        behavior='无有效题时原文讲解走完全部小要点；没有复活退役题', credit=credit)
    (root / 'first-theme-copy-evidence158.json').write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    if args.run:
        print(json.dumps(verify_first_theme('D:/MentorFlow/.local/test-tmp/points158'), ensure_ascii=False))
