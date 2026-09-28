"""Explicit opt-in: ordinary regressions never access credentials or network."""
import os
import json
import pytest


@pytest.mark.skipif(os.environ.get("MF_CLASS_EVAL") != "1", reason="真实课堂评测须显式启用，累计预算 ¥10")
def test_classroom_five_simulated_students_live():
    from .class_eval import PERSONAS, run
    output = run(os.environ.get("MF_CLASS_EVAL_PHASE", "before"), audit_only=os.environ.get("MF_CLASS_EVAL_AUDIT_ONLY") == "1")
    for persona in PERSONAS:
        assert json.loads((output/persona/"result.json").read_text(encoding="utf-8"))["status"] == "completed", persona
    assert not json.loads((output/"audit-errors.json").read_text(encoding="utf-8"))
