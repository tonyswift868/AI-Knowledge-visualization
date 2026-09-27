"""Paid experiment, skipped unless deliberately enabled; headless PlanFlow only."""
from datetime import datetime, timezone
import os

import pytest

from plan_parameter_eval import TEMP_ROOT, main


@pytest.mark.skipif(os.environ.get("MF_LIVE_EVAL") != "1", reason="Paid model experiment: set MF_LIVE_EVAL=1 explicitly")
def test_eval_plan_parameters_real_model():
    root = os.environ.get("MF_EVAL_ROOT") or str(TEMP_ROOT / ("mf04-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")))
    assert main(["--root", root]) == 0
