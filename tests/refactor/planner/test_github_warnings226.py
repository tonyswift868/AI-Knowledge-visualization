"""226 regression checks for GitHub import warning visibility in source cards."""
from datetime import date
import threading
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow

WARNING = "docs/chapter2.md 没有可用正文，已跳过。"
URL = "https://github.com/local-test/guide"


class Store:
    def __init__(self, source):
        self.source = source
        self.adopted = None

    def records(self, kind, plan_id):
        return [self.source] if kind == "sources" else []

    def set_source_adopted(self, plan_id, source_id, adopted):
        self.adopted = (source_id, adopted)


def _flow(source, data_root):
    flow = PlanFlow(None, lambda *_: None, date.today)
    flow.services = SimpleNamespace(store=Store(source), account_id="test-account", data_root=data_root)
    flow._invalidate_material_structure = lambda draft: None
    return flow


def _source():
    return dict(id="source-1", url=URL, title="local-test/guide", sha256="abc",
                char_count=123, warnings=[WARNING], adopted=True)


def _result():
    return dict(status="imported", source_id="source-1", original_url=URL,
                url=URL, title="local-test/guide", char_count=123,
                elapsed_seconds=0.01, warnings=[WARNING])


@pytest.mark.parametrize("status", ["imported", "existing"])
def test_github_warning_map_is_persisted_for_imported_and_existing_source(tmp_path, status):
    flow = _flow(_source(), tmp_path)
    flow.status = lambda *_: None
    result = _result()
    result["status"] = status
    flow._read_links = lambda *_args, **_kwargs: dict(results=[result], source_ids=["source-1"], cancelled=False)
    draft = dict(plan_id="a" * 32, phase="sources", sources=[], recommendations=[], messages=[],
                 source_read_warnings={URL: "网站目录读取失败，已改读单页。"})
    if status == "existing":
        draft["sources"] = [dict(card_id="existing-card", source_id="source-1", url=URL)]
        result.pop("warnings")  # Existing stored sources still carry the import warning.

    flow._import_links(draft, SimpleNamespace(cancel=threading.Event()), [URL])

    persisted = flow.load(draft["plan_id"])
    assert persisted["source_read_warnings"][URL] == "网站目录读取失败，已改读单页。\n" + WARNING


def test_recommended_card_adoption_copies_github_import_warning(tmp_path):
    flow = _flow(_source(), tmp_path)
    flow.status = lambda *_: None
    flow._read_links = lambda *_args, **_kwargs: dict(results=[_result()], source_ids=["source-1"], cancelled=False)
    card = dict(card_id="recommendation-1", url=URL, title="local-test/guide",
                user_provided=False, status="new")
    draft = dict(plan_id="b" * 32, phase="sources", sources=[], recommendations=[card], messages=[])

    assert flow.decide(draft, card["card_id"], True,
                       ctx=SimpleNamespace(cancel=threading.Event())) is True

    assert draft["source_read_warnings"][URL] == WARNING
    assert flow.load(draft["plan_id"])["source_read_warnings"][URL] == WARNING
