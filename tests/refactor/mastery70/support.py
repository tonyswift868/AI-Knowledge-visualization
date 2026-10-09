"""Persist one objective source question per point for state/accounting fixtures."""
from tests.refactor.integration38.support import bank_question


def seed_assessment(store, plan):
    for point in store.records('points', plan):
        question = bank_question(point['id'], point['module_id'], kind='fill',
                                 answer='依据正文', chunk=point['chunk_ids'][0])
        question['id'] = point['id']
        # These fixtures represent current, usable assessment, not pre-70 bank
        # records which v8 now correctly retires during upgrade and sync.
        question.update(kind='objective',objective_kind='fill',answer='依据正文',reference='',key_points=[],options=[])
        question.pop('distractor_check',None)
        for key in ('source','answer_source'):
            question[key]=dict(question[key],chunk_ids=[point['chunk_ids'][0]],block_ids=[point['chunk_ids'][0]])
        store.add_bank_questions(plan, point['module_id'], [question])
