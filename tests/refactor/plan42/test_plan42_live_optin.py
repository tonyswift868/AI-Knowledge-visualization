import os
import pytest


@pytest.mark.skipif(os.environ.get('MF_PLAN42_LIVE')!='1',reason='Paid dialogue is opt-in and capped at CNY 1')
def test_plan42_real_intake_optin():
    from tests.refactor.plan42.plan42_live_dialogue import run
    run()
