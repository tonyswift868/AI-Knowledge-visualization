"""Persist one objective source question per point for state/accounting fixtures."""
from tests.refactor.integration38.support import bank_question


def seed_assessment(store, plan):
    for point in store.records('points', plan):
        question = bank_question(point['id'], point['module_id'], kind='fill',
                                 answer='依据正文', chunk=point['chunk_ids'][0])
        question['id'] = point['id']
        store.add_bank_questions(plan, point['module_id'], [question])
