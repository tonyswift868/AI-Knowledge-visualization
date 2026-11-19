"""Legacy recovery attribution survives protocol sync and SQLite reopen."""
from datetime import timedelta
from pathlib import Path

import pytest

from refactor.learning.course.ledger import Ledger
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync
from tests.refactor.course_integration.test_store_sync_roundtrip import TODAY, build_plan
from tests.refactor.sync_course.test_sync_course import connection

@pytest.fixture(params=[False, True], ids=['snapshot-fallback', 'incremental'])
def devices186(connection, request, tmp_path):
    server, transport, login = connection
    server.incremental = request.param
    server.page_limit = 3
    first = CourseStore(login.identity.account_id, tmp_path / 'device-a')
    second = CourseStore(login.identity.account_id, tmp_path / 'device-b')
    try:
        yield server, transport, first, second
    finally:
        first.close()
        second.close()


def _recovery_state(store, today):
    ledger = store.load_ledger('plan')
    return ledger.status(today), ledger.attributed_credits(), ledger.to_dict()


def test_legacy_recovery_syncs_to_second_store_and_reopens_with_same_dates(devices186):
    _server, transport, a, b = devices186
    build_plan(a)
    old_day = TODAY + timedelta(days=1)
    query_day = TODAY + timedelta(days=3)

    a.add_credit('plan', 'subpoint:old-source', 'p1', 4, old_day, 'tentative')
    a.revoke_credits('plan', 'p1', event_id='legacy-failed-test', today=query_day)

    # Build the historical aggregate row produced by versions before BUG-120.
    # Its day is the recovery day, while the immutable revoked source retains
    # the actual learning date. This fixture changes only a disposable store.
    with a.transaction():
        a._add_credit_record('plan', 'relearn:direct:legacy-attempt:p1', 'p1', 4,
                             query_day, 'confirmed', allow_recovery=True)
    raw_before = a.records('credits', 'plan')
    original = next(row for row in raw_before if row['id'] == 'subpoint:old-source')
    legacy = next(row for row in raw_before if row['id'] == 'relearn:direct:legacy-attempt:p1')
    assert original['credit']['day'] == old_day.isoformat()
    assert legacy['credit']['day'] == query_day.isoformat()

    CourseSync(a, transport).sync()
    CourseSync(b, transport).sync()

    status_a, attributed_a, _ledger_a = _recovery_state(a, query_day)
    status_b, attributed_b, ledger_b = _recovery_state(b, query_day)
    assert status_a == status_b
    assert status_a.today_repaid_confirmed == 0
    assert attributed_a == attributed_b
    source_day_slice = next((credit, day, minutes) for credit, day, minutes in attributed_b
                            if credit.credit_id == legacy['id'])
    assert source_day_slice[1] == old_day and source_day_slice[2] == 4
    assert a.records('credits', 'plan') == raw_before
    assert b.records('credits', 'plan') == raw_before

    # The projection is part of the in-memory ledger snapshot contract too.
    reopened = Ledger.from_dict(ledger_b)
    assert reopened.status(query_day) == status_b
    assert reopened.attributed_credits() == attributed_b
