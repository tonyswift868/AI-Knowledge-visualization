"""Narrow test-only adapter for the proposed stable-subpoint-ID contract.

The real Store remains unchanged and its current rejection has a separate test.
This adapter retains every existing validation except the obsolete requirement
that a subpoint id share its current parent point's prefix.
"""
from contextlib import contextmanager
from copy import deepcopy
from types import MethodType
from unittest.mock import patch

from refactor.learning.course.store import _id
from refactor.learning.course import question_bank


@contextmanager
def allow_stable_subpoint_ids(store):
    original = store._validate_concept_fields

    def adapted(self, entity, plan, payload, *, references=True):
        if entity != 'points' or not isinstance(payload, dict) or 'subpoints' not in payload:
            return original(entity, plan, payload, references=references)
        value = deepcopy(payload)
        seen = set()
        for index, subpoint in enumerate(value['subpoints'], 1):
            sid = subpoint.get('id') if isinstance(subpoint, dict) else None
            _id(sid)
            if sid in seen:
                from refactor.learning.course.store import StoreError
                raise StoreError('INVALID_SUBPOINT_ID')
            seen.add(sid)
            # Only the throwaway validation copy is changed. All remaining point
            # contract checks run against real Store logic.
            subpoint['id'] = f"{value['id']}-s{index}"
        return original(entity, plan, value, references=references)

    store._validate_concept_fields = MethodType(adapted, store)
    try:
        yield store
    finally:
        store._validate_concept_fields = original


@contextmanager
def allow_question_subpoint_ids():
    """Narrow adapter for direct QuestionBank shape validation in contract tests.

    Store separately verifies that the referenced parent actually contains the
    subpoint. This adapter removes only the old parent-prefix relation before
    delegating all remaining question validation to the real function.
    """
    original = question_bank.validate_question

    def adapted(row):
        sid = row.get('subpoint_id')
        if sid is not None:
            _id(sid)
            value = dict(row)
            value.pop('subpoint_id', None)
            original(value)
            return row
        return original(row)

    with patch.object(question_bank, 'validate_question', adapted):
        yield
