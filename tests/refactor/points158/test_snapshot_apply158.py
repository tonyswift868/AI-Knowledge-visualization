"""Conditional integration verification against the D: disposable data copy.

Set MF158_COPY_DATA_ROOT and MF158_APPLY_READY to task artifacts. The fixture
copy is reclassified as complete only inside the test copy so an incomplete
one-module model response can exercise the future regroup contract.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace
import uuid

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course import point_materials, question_bank
from refactor.learning.course.store import CourseStore
from scripts import plan_restructure as cli
from tests.refactor.points158.proposed_contract158 import (
    allow_question_subpoint_ids, allow_stable_subpoint_ids,
)


DATA_ROOT = os.environ.get('MF158_COPY_DATA_ROOT')
STRUCTURE_FILE = os.environ.get('MF158_APPLY_READY')
pytestmark = pytest.mark.skipif(not DATA_ROOT or not STRUCTURE_FILE,
                                reason='requires explicit D: points158 task copy and apply-ready input')

ACCOUNT_ID = 'mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'
PLAN_ID = '029b076315034c00bbd6842a5c9c31d9'


class _FakeGateway:
    def __init__(self):
        self.calls = []

    def complete(self, purpose, messages, **kwargs):
        assert purpose == 'teacher'
        self.calls.append(copy.deepcopy(messages))
        text = '我们从当前主题的第一个小要点开始，依据原文一起梳理。'
        if kwargs.get('on_text'):
            kwargs['on_text'](text)
        return SimpleNamespace(text=text)


class _NoopMaterials:
    def prepare_module(self, store, plan_id, module_id, ctx, progress):
        progress(1, 1, '测试替身：资料已可用')

    def prepare_point(self, store, plan_id, point, ctx):
        return None

    def pick_question(self, *args, **kwargs):
        return None

    def mark_used(self, *args, **kwargs):
        return None


def _database_dump(store):
    return '\n'.join(store._db.iterdump())


def _canonical_without_point_ids(question):
    return {key: value for key, value in question.items() if key != 'point_ids'}


def test_snapshot158_proposed_contract_copy_apply_sync_rollback_and_classroom(tmp_path, monkeypatch):
    source_root = Path(DATA_ROOT).resolve()
    task_root = Path(r'D:\MentorFlow\.local\test-tmp\points158').resolve()
    source_root.relative_to(task_root)
    source_structure = Path(STRUCTURE_FILE).resolve()
    source_structure.relative_to(task_root)

    device_a_root = tmp_path / 'device-a'
    device_b_root = tmp_path / 'device-b'
    shutil.copytree(source_root, device_a_root)
    shutil.copytree(source_root, device_b_root)
    structure = json.loads(source_structure.read_text(encoding='utf-8'))
    structure['complete'] = True
    structure.pop('quality_review', None)
    structure['today'] = '2026-10-17'
    structure_path = tmp_path / 'apply-ready-test-fixture.json'
    structure_path.write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding='utf-8')

    with CourseStore(ACCOUNT_ID, device_a_root) as store:
        initial_dump = _database_dump(store)
        original_position = copy.deepcopy(store.classroom_position(PLAN_ID))
        original_answers = store.records('question_answers', PLAN_ID)
        original_credits = store.records('credits', PLAN_ID)
        original_questions = {q['id']: q for q in store.records('question_bank', PLAN_ID)}

        # Evidence that the production Store currently rejects this exact copied
        # proposal before the narrowly scoped future-contract adapter is enabled.
        current_contract = cli.apply_structure(store, PLAN_ID, structure, dry_run=True)
        assert not current_contract['can_apply']
        assert any('INVALID_SUBPOINT_ID' in issue for issue in current_contract['issues'])

        with allow_stable_subpoint_ids(store):
            preview = cli.apply_structure(store, PLAN_ID, structure, dry_run=True)
            assert preview['can_apply'], preview['issues']
            report, backup = cli.execute(store, PLAN_ID, structure, preview['preview_token'])

        assert store.classroom_position(PLAN_ID) == original_position
        assert store.records('question_answers', PLAN_ID) == original_answers
        assert store.records('credits', PLAN_ID) == original_credits
        assert len(store.records('question_bank', PLAN_ID)) == len(original_questions)
        assert report['regroup_subpoints'] is True

        active_points = store.records('points', PLAN_ID)
        owner_by_sid = {sub['id']: point for point in active_points
                        for sub in point.get('subpoints', [])}
        current_questions = {q['id']: q for q in store.records('question_bank', PLAN_ID)}
        migrated_count = 0
        for qid, before in original_questions.items():
            after = current_questions[qid]
            assert _canonical_without_point_ids(after) == _canonical_without_point_ids(before)
            if before.get('subpoint_id') in owner_by_sid:
                owner = owner_by_sid[before['subpoint_id']]
                assert owner['id'] in after['point_ids']
                if owner['id'] != before['point_ids'][0]:
                    migrated_count += 1
            assert after.get('role') == before.get('role')
        assert migrated_count == report['regrouped_questions']
        # Qbank rows including retired rows are migrated by sid, never retired
        # as a side effect of the regroup.
        assert {q['id']: q.get('role') for q in current_questions.values()} == {
            q['id']: q.get('role') for q in original_questions.values()}

        # Send the exact pending sync payload to a second copy under the same
        # narrow adapter. This exercises real Store payload and apply_remote
        # validation, while retaining original stable subpoint IDs on disk.
        with CourseStore(ACCOUNT_ID, device_b_root) as remote:
            pending = store.pending_records(10000)
            with allow_stable_subpoint_ids(remote):
                remote.apply_remote([(record, index + 1) for index, record in enumerate(pending)])
            remote_points = remote.records('points', PLAN_ID)
            remote_owner = {sub['id']: point['id'] for point in remote_points
                            for sub in point.get('subpoints', [])}
            remote_questions = {q['id']: q for q in remote.records('question_bank', PLAN_ID)}
            assert len(remote_questions) == len(current_questions)
            assert all(q.get('role') == current_questions[qid].get('role')
                       for qid, q in remote_questions.items())
            assert all(q['point_ids'][0] == remote_owner[q['subpoint_id']]
                       for q in remote_questions.values() if q.get('subpoint_id') in remote_owner)

            # Reopening continues exactly at the saved user cursor. Then a
            # separately selected first new theme is taught through real
            # Classroom logic with only the Gateway replaced by a fake.
            fake_gateway = _FakeGateway()
            settings = SimpleNamespace(load=lambda: dict(
                teacher_style='warm', detail='brief', examples='few',
                analogy_domains=['life'], refresh_time='04:00'))
            services = SimpleNamespace(store=remote, gateway=fake_gateway,
                                       account_id=ACCOUNT_ID, data_root=device_b_root,
                                       settings=settings)
            room = Classroom(services, lambda: date(2026, 10, 17),
                             now=lambda: datetime(2026, 10, 17, 12, tzinfo=timezone.utc))
            monkeypatch.setattr(Classroom, '_materials', staticmethod(lambda: _NoopMaterials()))
            ctx = SimpleNamespace(cancel=threading.Event(), stream=lambda _text: None)
            resumed = room.perform(PLAN_ID, 'enter', ctx, (), uuid.uuid4().hex)
            assert not resumed.get('failure')
            saved_state = remote.classroom_position(PLAN_ID)['state']
            cursor_id = (saved_state.get('point_id')
                         or (saved_state.get('concept') or {}).get('point_id'))
            assert saved_state['module_id'] == original_position['state']['module_id']
            assert cursor_id == (original_position['state'].get('point_id')
                                 or (original_position['state'].get('concept') or {}).get('point_id'))

            # Exercise the real question-bank selector and question validator
            # against re-homed, source-backed questions. Only the obsolete
            # parent-prefix relation is adapted for this future contract.
            selected_question = None
            first_theme = None
            with allow_question_subpoint_ids():
                for scope in structure['modules']:
                    proposed_ids = {p['id'] for p in scope['points']}
                    candidates = sorted((p for p in remote_points
                                         if p['module_id'] == scope['module_id'] and p['id'] in proposed_ids),
                                        key=lambda p: (p['position'], p['id']))
                    for candidate in candidates:
                        question = point_materials.pick_question(
                            remote, PLAN_ID, candidate['id'], 'module')
                        if question and question_bank.checked_question(question):
                            question_bank.validate_question(question)
                            selected_question, first_theme = question, candidate
                            break
                    if selected_question:
                        break
            assert selected_question is not None, '已重分组主题没有可选的现存合格题'
            assert selected_question['subpoint_id'] in {s['id'] for s in first_theme['subpoints']}
            assert first_theme['chunk_ids']
            assert all(remote._get('source_chunks', PLAN_ID, cid) for cid in first_theme['chunk_ids'])
            assert first_theme['subpoints'][0].get('notes'), '所选新主题没有可核对讲义'

            first_module_id = first_theme['module_id']
            module = remote._get('modules', PLAN_ID, first_module_id)
            module['material_preparation'] = dict(module.get('material_preparation') or {}, state='done')
            module.pop('question_count', None)
            with remote.transaction():
                remote._put('modules', PLAN_ID, module)
            module_points = sorted((p for p in remote.records('points', PLAN_ID)
                                    if p['module_id'] == first_module_id),
                                   key=lambda p: (p['position'], p['id']))
            next_state = copy.deepcopy(remote.classroom_position(PLAN_ID)['state'])
            next_state.update(module_id=first_module_id,
                              index=next(i for i, p in enumerate(module_points) if p['id'] == first_theme['id']),
                              phase='learning', step='teach', pending=None, test=None, check=None,
                              in_session=True)
            next_state.pop('concept', None)
            next_state.pop('conversation', None)
            remote.save_classroom_position(PLAN_ID, next_state)
            # Advance through every child using the actual Classroom API. The
            # gateway and module-preparation worker alone are deterministic
            # test doubles; teacher/lesson orchestration remains real.
            for _ in range(len(first_theme['subpoints']) + 1):
                taught = room.perform(PLAN_ID, 'next', ctx, (), uuid.uuid4().hex)
                assert not taught.get('failure'), taught
            assert fake_gateway.calls
            teacher_requests = json.dumps(fake_gateway.calls, ensure_ascii=False)
            assert all(sub['id'] in teacher_requests for sub in first_theme['subpoints']), \
                '真实课堂 next 循环没有逐个覆盖全部小要点'
            note_text = first_theme['subpoints'][0]['notes'][0]['text']
            assert note_text in teacher_requests, \
                '老师请求未携带所选新主题的出处讲义'
            learned_credit = remote._get('credits', PLAN_ID, 'learn:' + first_theme['id'])
            assert learned_credit['credit']['unit_id'] == first_theme['id']
            assert learned_credit['credit']['minutes'] == first_theme['minutes']
            final_state = remote.classroom_position(PLAN_ID)['state']
            assert final_state['module_id'] == first_module_id
            assert final_state['index'] > next(i for i, p in enumerate(module_points) if p['id'] == first_theme['id'])

        # Roll back the apply on device A before any classroom writes occurred
        # there, and verify the whole SQLite logical dump is identical.
        rolled = cli.rollback(device_a_root, ACCOUNT_ID, PLAN_ID, backup)
        assert rolled['local_restored'] is True

    with CourseStore(ACCOUNT_ID, device_a_root) as restored:
        assert _database_dump(restored) == initial_dump
