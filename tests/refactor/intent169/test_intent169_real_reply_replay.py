import json
from pathlib import Path

import pytest

from refactor.learning.course.plan_intent import format_error_diagnostic, parse_intents
from refactor.learning.course.planner_agents import AgentFormatError
from tests.refactor.plan_intent.intent_cases import context


FIXTURE = Path(__file__).parent / "fixtures" / "real_questions_import_6_of_6.json"


def test_intent169_real_questions_reply_replay_stays_rejected():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["observed_identical_count"] == "6/6"
    response = json.dumps(fixture["response"], ensure_ascii=False)

    # Real captured response contains only model output, not private form context.
    with pytest.raises(AgentFormatError) as caught:
        parse_intents(response, context("questions"))
    assert format_error_diagnostic(caught.value) == {
        "field": "phase", "reason": "import_sources_not_allowed"
    }


def test_intent169_real_fixed_reply_preserves_preference_and_full_link():
    fixture = json.loads((FIXTURE.parent / "real_questions_update_form.json").read_text(encoding="utf-8"))
    parsed = parse_intents(json.dumps(fixture["response"], ensure_ascii=False), context("questions"))
    assert len(parsed) == 1
    assert parsed[0].kind == "update_form"
    assert parsed[0].params["only_mine"] is True
    assert parsed[0].params["notes_append"] == (
        "按模块串讲一遍。资料我自己提供，只用这个仓库里的资料，不用另外找："
        "https://github.com/tonyswift868/mentorflow-guide"
    )
