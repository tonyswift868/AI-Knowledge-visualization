"""Unmerged batch42 material contract only for explicitly seeded classroom fixtures."""
import pytest


@pytest.fixture(autouse=True)
def classroom_seeded_material_contract42(monkeypatch):
    from refactor.learning.app.classroom import Classroom
    from refactor.learning.course import point_materials, question_bank

    class Boundary:
        def pick_question(self,store,plan,point,purpose,exclude_ids=()):
            if not hasattr(store,'_offline_quick42'):
                return point_materials.pick_question(store,plan,point,purpose,exclude_ids=exclude_ids)
            role='quick' if purpose=='quick' else 'bank'
            rows=[q for q in store.records('question_bank',plan) if q['role']==role]
            return question_bank.select_question(rows,point,purpose,exclude_ids)

        def mark_used(self,store,plan,qid,purpose):
            quick=next((q for q in getattr(store,'_offline_quick42',[]) if q['id']==qid),None)
            if quick is None:return point_materials.mark_used(store,plan,qid,purpose)
            quick['uses'].append(dict(purpose=purpose,at='2026-10-03T00:00:00+00:00'))

        def prepare_point(self,store,plan,point,ctx):
            if not hasattr(store,'_offline_quick42'):return point_materials.prepare_point(store,plan,point,ctx)
            return dict(point_id=point['id'],questions=sum(point['id'] in q['point_ids'] and q['role']=='bank' for q in store.records('question_bank',plan)))

        def prepare_module(self,store,plan,module,ctx,progress):
            if not hasattr(store,'_offline_quick42'):return point_materials.prepare_module(store,plan,module,ctx,progress)
            points=[p for p in store.records('points',plan) if p['module_id']==module]
            progress(len(points),len(points),'离线原文和题目已准备')
            return dict(points=len(points),cloze_points=0,cloze_heavy=False)

    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:Boundary()))
