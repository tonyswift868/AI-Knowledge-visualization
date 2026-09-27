"""The only paid pytest entry. Default test collection never reads credentials."""
import os
import pytest
from .plan_dialogue_live import run_live


@pytest.mark.skipif(os.environ.get("MF_LIVE_EVAL") != "1", reason="real model evaluation requires MF_LIVE_EVAL=1; costs money")
def test_plan_dialogue_live_a_contracts():
    _, records = run_live()
    assert len(records) == 33
    assert all(r["status"] == "PASS" for r in records), [
        (r["id"], [c["name"] for c in r["checks"] if not c["passed"]]) for r in records if r["status"] != "PASS"]
