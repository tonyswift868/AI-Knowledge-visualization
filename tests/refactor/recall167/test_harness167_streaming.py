import json
import base64
from dataclasses import asdict

import pytest

from tests.refactor.guide165.check_guide_import165 import (
    _load_verified_source,
    _install_streaming_gateway,
    _question_type_counts,
    _validated_live_budget,
    build_parser,
)


class FakeGateway:
    def __init__(self):
        self.kwargs = []

    def complete(self, purpose, messages, **kwargs):
        self.kwargs.append((purpose, messages, kwargs))
        callback = kwargs.get("on_text")
        if callback:
            callback("stream fragment")
        return "ok"


def test_harness_defaults_all_live_purposes_to_streaming_callback():
    gateway = _install_streaming_gateway(FakeGateway())
    calls = [
        ("outline", [{"role": "user", "content": "classify"}], {}),
        ("generate", [{"role": "user", "content": "cut"}], {}),
        ("point_materials", [{"role": "user", "content": "notes"}], {}),
    ]
    for purpose, messages, kwargs in calls:
        assert gateway.complete(purpose, messages, **kwargs) == "ok"

    assert [row[0] for row in gateway.kwargs] == ["outline", "generate", "point_materials"]
    assert all(call[2].get("on_text") is not None for call in gateway.kwargs)


def test_harness_preserves_existing_stream_callback():
    gateway = _install_streaming_gateway(FakeGateway())
    fragments = []
    callback = fragments.append

    gateway.complete("point_materials", [], on_text=callback)

    assert gateway.kwargs[0][2]["on_text"] is callback
    assert fragments == ["stream fragment"]


def test_streaming_installer_is_idempotent():
    gateway = _install_streaming_gateway(FakeGateway())
    assert _install_streaming_gateway(gateway) is gateway
    gateway.complete("outline", [])
    assert len(gateway.kwargs) == 1


def test_streaming_installer_replaces_explicit_none_callback():
    gateway = _install_streaming_gateway(FakeGateway())
    gateway.complete("point_materials", [], on_text=None)
    assert gateway.kwargs[0][2]["on_text"] is not None


def test_question_type_counts_separate_originals_and_cloze():
    assert _question_type_counts([
        {"kind": "recall", "origin": "found"},
        {"kind": "objective", "origin": "found"},
        {"kind": "choice", "origin": "cloze"},
    ]) == {"original_recall": 1, "original_objective": 1, "cloze": 1}


def test_live_budget_accepts_task_cap_and_rejects_over_cap():
    assert _validated_live_budget("15") == 15
    with pytest.raises(ValueError, match="不超过 15 元"):
        _validated_live_budget("15.01")
    with pytest.raises(ValueError):
        _validated_live_budget("0")


def test_cached_source_can_run_live_without_network_refetch(tmp_path):
    from refactor.learning.course.github_repo import import_repo

    repo_url = "https://github.com/example/guide"
    metadata_url = "https://api.github.com/repos/example/guide"
    tree_url = metadata_url + "/git/trees/main?recursive=1"
    raw_url = "https://raw.githubusercontent.com/example/guide/main/docs/a.md"
    responses = {
        metadata_url: json.dumps({"full_name": "example/guide", "default_branch": "main",
                                  "description": "Fixture"}).encode(),
        tree_url: json.dumps({"tree": [{"path": "docs/a.md", "type": "blob", "size": 25}]}).encode(),
        raw_url: b"# Chapter A\n\nA verified fact.\n",
    }
    source = import_repo(repo_url, fetch=lambda url, cancel=None: responses[url])
    payload = {
        "url": source.url, "content_sha256": source.content_sha256,
        "verified_import_sha256": source.content_sha256,
        "document": asdict(source.document), "selected_files": ["docs/a.md"],
        "fetch_responses": [{"url": url, "body_b64": base64.b64encode(body).decode("ascii")}
                            for url, body in responses.items()],
    }
    cache_path = tmp_path / "source.json"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    source, files = _load_verified_source(cache_path)

    assert source.content_sha256 == payload["content_sha256"]
    assert source.document.blocks[0].text == "Chapter A"
    assert source.document.chunks[0].text
    assert files == ["docs/a.md"]


def test_live_cli_accepts_verified_cache_and_task_budget():
    args = build_parser().parse_args([
        "--cached-source", "D:/MentorFlow/.local/test-tmp/recall167/input.json",
        "--live", "--output", "D:/MentorFlow/.local/test-tmp/recall167/live",
        "--budget", "15",
    ])
    assert args.cached_source.name == "input.json"
    assert _validated_live_budget(args.budget) == 15
