import json
import logging

import pytest

from refactor.learning.course.plan_intent import (
    Intent,
    format_error_diagnostic,
    parse_intents,
    INTENT_SYSTEM,
)
from refactor.learning.course import plan_intent
from refactor import runtime_logging
from refactor.learning.course.planner_agents import AgentFormatError
from intent_cases import context


def response(*intents):
    return json.dumps({"intents": list(intents), "reply": ""}, ensure_ascii=False)


@pytest.mark.parametrize("only_mine", [True, False])
def test_intent169_only_mine_requires_explicit_boolean(only_mine):
    params = {"only_mine": only_mine, "notes_append": "只用这份资料 https://example.test/a"}
    assert parse_intents(response({"kind": "update_form", **params}), context("questions")) == [
        Intent("update_form", params)
    ]


@pytest.mark.parametrize("value", ["true", 1, 0, None, [], {}])
def test_intent169_only_mine_rejects_non_boolean_values(value):
    with pytest.raises(AgentFormatError):
        parse_intents(response({"kind": "update_form", "only_mine": value}), context("questions"))


def test_intent169_question_step_keeps_url_in_notes_and_forbids_import_action():
    url = "https://example.test/course?a=1&b=2"
    instruction = f"只用我提供的资料，不要另外找资料：{url}"
    parsed = parse_intents(response(
        {"kind": "update_form", "only_mine": True, "notes_append": instruction},
    ), context("questions"))
    assert parsed == [Intent("update_form", {"only_mine": True, "notes_append": instruction})]
    with pytest.raises(AgentFormatError):
        parse_intents(response({"kind": "import_sources", "urls": [url]}), context("questions"))
    assert "只用一个 update_form" in INTENT_SYSTEM


@pytest.mark.parametrize("raw,phase,expected", [
    ("not JSON", "questions", {"field": "response", "reason": "invalid_json"}),
    ('{"intents":[],"reply":""}', "questions", {"field": "intents", "reason": "invalid_fields"}),
    (response({"kind": "import_sources", "urls": ["https://example.test"]}), "questions",
     {"field": "phase", "reason": "import_sources_not_allowed"}),
    (response({"kind": "import_sources", "urls": ["file:///secret"]}), "sources",
     {"field": "urls", "reason": "invalid_url_list"}),
])
def test_intent169_diagnostic_uses_safe_static_categories(raw, phase, expected):
    with pytest.raises(AgentFormatError) as caught:
        parse_intents(raw, context(phase))
    assert format_error_diagnostic(caught.value) == expected


def test_intent169_diagnostic_never_exposes_unknown_exception_text():
    assert format_error_diagnostic(ValueError("user URL https://private.test/key=secret")) == {
        "field": "response", "reason": "invalid_format"
    }
    assert format_error_diagnostic(AgentFormatError()) == {
        "field": "response", "reason": "invalid_format"
    }
    assert format_error_diagnostic(AgentFormatError(None)) == {
        "field": "response", "reason": "invalid_format"
    }


def test_intent169_diagnostic_maps_only_mine_validation_to_static_label():
    with pytest.raises(AgentFormatError) as caught:
        parse_intents(response({"kind": "update_form", "only_mine": "yes"}), context("questions"))
    assert format_error_diagnostic(caught.value) == {"field": "only_mine", "reason": "invalid_value"}


@pytest.mark.parametrize("message,expected", [
    ("empty update_form", {"field": "update_form", "reason": "empty_update"}),
    ("combined notes too long", {"field": "notes_append", "reason": "limit_exceeded"}),
    ("special day fields", {"field": "special_days", "reason": "invalid_fields"}),
    ("special day range/duplicate", {"field": "special_days", "reason": "invalid_range_or_duplicate"}),
    ("answer phase", {"field": "phase", "reason": "answer_not_allowed"}),
    ("supplement phase", {"field": "phase", "reason": "supplement_not_allowed"}),
    ("select_modules phase", {"field": "phase", "reason": "select_modules_not_allowed"}),
    ("module selections", {"field": "modules", "reason": "invalid_selection"}),
    ("delete_map phase", {"field": "phase", "reason": "delete_map_not_allowed"}),
    ("delete_map selections", {"field": "delete_map", "reason": "invalid_selection"}),
    ("delete_map point", {"field": "points", "reason": "invalid_selection"}),
    ("empty delete_map", {"field": "delete_map", "reason": "empty_selection"}),
    ("edit_modules phase", {"field": "phase", "reason": "edit_modules_not_allowed"}),
    ("module moves", {"field": "modules", "reason": "invalid_moves"}),
    ("module move fields", {"field": "moves", "reason": "invalid_fields"}),
    ("module move reference/position", {"field": "moves", "reason": "invalid_reference_or_position"}),
    ("proceed step", {"field": "step", "reason": "invalid_value"}),
    ("ask one question", {"field": "question", "reason": "multiple_questions"}),
    ("abandon scope items", {"field": "items", "reason": "invalid_selection"}),
    ("refuse reason must be Chinese", {"field": "reason", "reason": "invalid_language"}),
    ("invalid special day date", {"field": "special_days", "reason": "invalid_date"}),
    ("invalid start date", {"field": "start", "reason": "invalid_date"}),
    ("invalid end date", {"field": "end", "reason": "invalid_date"}),
    ("invalid instruction", {"field": "instruction", "reason": "invalid_value"}),
    ("invalid question answer", {"field": "answer", "reason": "invalid_value"}),
])
def test_intent169_known_parser_failures_have_specific_diagnostics(message, expected):
    assert format_error_diagnostic(AgentFormatError(message)) == expected


def test_intent169_all_static_diagnostic_labels_survive_json_formatter():
    for field, reason in set(plan_intent._FORMAT_DIAGNOSTICS.values()):
        record = logging.LogRecord("refactor", logging.INFO, __file__, 1, "", (), None)
        record.mf_event = "model"
        record.mf_fields = {"field": field, "reason": reason}
        logged = json.loads(runtime_logging.DiagnosticFormatter().format(record))
        assert logged["field"] == field
        assert logged["reason"] == reason
