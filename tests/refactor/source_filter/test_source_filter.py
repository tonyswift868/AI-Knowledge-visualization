"""Offline contract tests: no web reading, credentials, or model invocation."""
import ast
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys

import pytest

from refactor.learning.course import source_filter as sf
from refactor.learning.course.source_filter import (
    DomainRule, RawHit, SourceListsError, github_repo, is_probable_fork,
    load_lists, normalize_url, screen,
)


@pytest.fixture
def lists():
    return replace(load_lists(),whitelist=(DomainRule('wikipedia.org','test legacy suffix rule','Wikipedia','encyclopedia'),))


@pytest.mark.parametrize("value", [None, "", "   ", "file:///a", "javascript:alert(1)",
    "ftp://example.com", "example.com/page", "https:///missing-host", "https://",
    "https://user:secret@example.com", "https://@example.com", "https://bad host.test/a",
    "https://example.com:bad/a", "https://example.com:99999/a", "https://example.com:0/a",
    "https://example.com:/a", "https://[::broken]/a", "https://bad_.example/a",
    "https://-bad.example/a", "https://example.com/a%Q1", "https://example.com/a%",
    "https://example.com\\@wikipedia.org", "https://example.com/a\npath",
    "https://raw.githubusercontent.com/owner/repo/main"])
def test_invalid_urls_are_dropped(value):
    assert normalize_url(value) is None
    assert screen([RawHit(value)]) == []


def test_tracking_cleanup_and_preservation_of_real_query_parameters():
    raw = " HTTPS://Example.COM:443/a/%7Euser/?%75tm_source=x&UTM_medium=y&spm=1&from=2&share=3&fbclid=4&q=a+b&api-mode=responses&x=&x=2#section "
    assert normalize_url(raw) == "https://example.com/a/~user?q=a+b&api-mode=responses&x=&x=2"
    assert normalize_url("http://EXAMPLE.COM.:80/") == "http://example.com"
    assert normalize_url("https://EXAMPLE.COM:8443/a/") == "https://example.com:8443/a"
    assert normalize_url("https://[2001:db8::1]:443/a/") == "https://[2001:db8::1]/a"


@pytest.mark.parametrize("value", [
    "http://arxiv.org/abs/2210.03629v1/", "https://arxiv.org/pdf/2210.03629v2.pdf#page=1",
    "https://export.arxiv.org/pdf/2210.03629", "http://web3.arxiv.org/abs/2210.03629v19",
    "https://www.arxiv.org/html/2210.03629v3?download=1",
])
def test_arxiv_aliases_versions_and_formats(value):
    assert normalize_url(value) == "https://arxiv.org/abs/2210.03629"


def test_old_arxiv_ids_and_arxiv_impostors():
    assert normalize_url("https://export.arxiv.org/pdf/hep-th/9901001v3.pdf") == "https://arxiv.org/abs/hep-th/9901001"
    assert normalize_url("https://arxiv.org/help/") == "https://arxiv.org/help"
    assert normalize_url("https://arxiv.org/pdf/not-a-paper") is None
    assert normalize_url("https://arxiv.org.evil.test/pdf/2210.03629") == "https://arxiv.org.evil.test/pdf/2210.03629"


@pytest.mark.parametrize("value", ["https://GitHub.com/OpenAI/OpenAI-Python",
    "https://github.com/OPENAI/openai-python.git", "https://raw.githubusercontent.com/OPENAI/OpenAI-Python/main/README.md"])
def test_github_identity_is_case_insensitive(value):
    assert github_repo(value) == ("openai", "openai-python")


@pytest.mark.parametrize("value", ["https://github.com/openai", "https://github.com/orgs/openai",
    "https://github.com/topics/python", "https://github.com.evil.test/openai/repo",
    "https://example.com/openai/repo", "https://github.com/openai/.."])
def test_not_a_github_repository(value):
    assert github_repo(value) is None


def test_raw_conversion_preserves_file_case_ref_identity_and_order(lists):
    values = [RawHit("https://github.com/openai/test/blob/main/README.md#one"),
        RawHit("https://raw.githubusercontent.com/OpenAI/Test/main/README.md?utm_source=two"),
        RawHit("https://raw.githubusercontent.com/openai/test/master/README.md")]
    result = screen(iter(values), lists=lists)
    assert result[1].url == result[0].url
    assert result[1].original_url == values[1].url
    assert result[1].reasons == ("raw_file", "duplicate")
    assert result[1].duplicate_of == result[0].url
    assert result[2].url != result[0].url and result[2].duplicate_of is None


def test_fork_heuristic_sees_original_later_in_batch(lists):
    values = [RawHit("https://github.com/wshao12/hello-agents"),
        RawHit("https://github.com/DATAWHALECHINA/hello-agents/blob/main/a.md"),
        RawHit("https://github.com/wshao12/hello-agents/blob/main/b.md")]
    result = screen(values, lists=lists)
    assert result[0].tier == result[2].tier == "neutral"
    assert all("possible_fork" in result[i].reasons for i in (0, 2))
    assert result[1].tier == "whitelist" and "possible_fork" not in result[1].reasons


def test_unknown_owners_keep_first_owner_and_known_owners_are_both_retained(lists):
    result = screen([RawHit("https://github.com/first/demo"),
        RawHit("https://github.com/first/demo/blob/main/a"),
        RawHit("https://github.com/second/demo"), RawHit("https://github.com/first/other")], lists=lists)
    assert ["possible_fork" in hit.reasons for hit in result] == [False, False, True, False]
    official = screen([RawHit("https://github.com/openai/demo"), RawHit("https://github.com/microsoft/demo")], lists=lists)
    assert all(hit.tier == "whitelist" and "possible_fork" not in hit.reasons for hit in official)


def test_precedence_and_fork_capping_never_promote_blocked_or_low(lists):
    values = [RawHit("https://github.com/first/demo"), RawHit("https://github.com/second/demo")]
    white = replace(lists, whitelist=(DomainRule("github.com", "test", "test", "official_docs"),))
    assert [hit.tier for hit in screen(values, lists=white)] == ["whitelist", "neutral"]
    low = replace(white, low=(DomainRule("github.com", "test low"),))
    assert [hit.tier for hit in screen(values, lists=low)] == ["low", "low"]
    blocked = replace(low, blocked=(DomainRule("github.com", "test blocked"),))
    assert [hit.tier for hit in screen(values, lists=blocked)] == ["blocked", "blocked"]


def test_normalization_cannot_bypass_original_host_deny_rule(lists):
    lists = replace(lists, blocked=(DomainRule("raw.githubusercontent.com", "test original host denial"),))
    hit = screen([RawHit("https://raw.githubusercontent.com/openai/test/main/README.md")], lists=lists)[0]
    assert hit.tier == "blocked" and "blocked_site" in hit.reasons and "raw_file" in hit.reasons


def test_suffix_boundaries_candidates_and_shared_hosts(lists):
    urls = ["https://en.wikipedia.org/wiki/Python", "https://evilwikipedia.org/page",
        "https://developers.openai.com.evil.test/docs", "https://someone.github.io/docs",
        "https://github.com/someone/project", "https://pkg.go.dev/example.test/demo",
        "https://huggingface.co/datasets/someone/demo"]
    result = screen(map(RawHit, urls), lists=lists)
    assert result[0].tier == "whitelist" and result[0].category == "encyclopedia"
    assert all(item.tier == "neutral" for item in result[1:])


@pytest.mark.parametrize("url", ["https://library.webvpna.tsinghua.edu.cn/paper",
    "https://WEBVPN.proxy.test/paper", "https://a.webvpn42.example.edu/paper"])
def test_webvpn_blocks_even_under_whitelisted_university(url, lists):
    result = screen([RawHit(url)], lists=lists)[0]
    assert result.tier == "blocked" and "webvpn_proxy" in result.reasons


@pytest.mark.parametrize("kind,title", [("adult", "色情视频下载"), ("adult", "Free PORN videos"),
    ("gambling", "在线博彩赌场"), ("gambling", "Best online casino"),
    ("piracy", "某软件破解版下载"), ("piracy", "Download pirated software")])
def test_chinese_and_english_keywords_override_whitelist(kind, title, lists):
    result = screen([RawHit("https://en.wikipedia.org/test", title)], lists=lists)[0]
    assert result.tier == "blocked" and result.category == kind and kind in result.reasons


@pytest.mark.parametrize("url,kind", [("https://example.test/%70%6F%72%6E", "adult"),
    ("https://example.test/online-casino", "gambling"), ("https://example.test/cracked_software", "piracy"),
    ("https://example.test/%2570%256f%2572%256e", "adult"),
    ("https://freeporn.example.test", "adult"), ("https://onlinecasino.example.test", "gambling"),
    ("https://keygen-download.example.test", "piracy")])
def test_obvious_encoded_url_keywords(url, kind, lists):
    assert kind in screen([RawHit(url)], lists=lists)[0].reasons


def test_keywords_avoid_ambiguous_learning_terms_and_ignore_query_snippet(lists):
    result = screen([RawHit("https://example.test/learning", "Adult education in Essex: free PDF textbook",
        snippet="online casino", query="pornography")], lists=lists)[0]
    assert result.tier == "neutral" and not result.reasons
    title = "ＰＯＲＮ videos"
    assert "adult" in screen([RawHit("https://example.test/page", title)], lists=lists)[0].reasons


def test_default_draft_is_immutable_and_every_entry_has_reason(lists):
    lists=load_lists()
    assert lists.version==2 and lists.type_rules and lists.directory
    assert len({rule.match for rule in lists.whitelist}) == len(lists.whitelist)
    assert all(rule.reason for group in (lists.whitelist, lists.low, lists.blocked, lists.candidates) for rule in group)
    assert all(entry.reason for entry in lists.github_orgs)
    assert any('edu.cn' in rule['suffixes'] for rule in lists.type_rules)
    assert not any(rule.match in {"github.com", "github.io", "csdn.net", "huggingface.co"} for rule in lists.whitelist)
    with pytest.raises(FrozenInstanceError):
        lists.version = 2
    with pytest.raises(TypeError):
        lists.blocked_keywords["adult"] = ()


@pytest.mark.parametrize("change,field", [
    (lambda value: value.pop("whitelist"), "missing keys"),
    (lambda value: value.update(version=True), "version"),
    (lambda value: value.update(updated="not-a-date"), "updated"),
    (lambda value: value.update(low={}), "low"),
    (lambda value: value["whitelist"][0].update(category="untrusted"), "category"),
    (lambda value: value["whitelist"][0].update(reason=""), "reason"),
    (lambda value: value["whitelist"][0].update(match="https://wikipedia.org"), "match"),
    (lambda value: value["whitelist"].append(value["whitelist"][0]), "duplicate domain"),
    (lambda value: value["blocked"][0].update(match=".*(evil)"), "match"),
    (lambda value: value["github_orgs"][0].update(owner="bad/owner"), "owner"),
    (lambda value: value["blocked_keywords"].update(adult=[""]), "adult"),
    (lambda value: value["blocked_keywords"].update(gambling=["casino", "CASINO"]), "duplicate"),
])
def test_bad_list_schema_reports_path_and_field(tmp_path, change, field):
    source = Path(sf.__file__).with_name("data") / "source_lists.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    data['whitelist']=[dict(match='wikipedia.org',brand='Wikipedia',category='encyclopedia',reason='test v1 domain schema')]
    change(data)
    path = tmp_path / "bad-lists.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SourceListsError) as error:
        load_lists(path)
    assert str(path) in str(error.value) and field in str(error.value)


@pytest.mark.parametrize("text", ['{"version": 1, "version": 2}', "{bad-json", "[]"])
def test_bad_json_is_not_silently_ignored(tmp_path, text):
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SourceListsError, match="invalid.json"):
        load_lists(path)


def test_missing_file_has_clear_error(tmp_path):
    with pytest.raises(SourceListsError, match="missing.json"):
        load_lists(tmp_path / "missing.json")


@pytest.mark.parametrize("fork", [True, False])
def test_injected_fork_lookup_true_and_false(fork):
    calls = []
    def fetch(url):
        calls.append(url)
        return {"fork": fork}
    assert is_probable_fork("OpenAI", "Example", fetch=fetch) is fork
    assert calls == ["https://api.github.com/repos/openai/example"]


def test_injected_fork_lookup_failure_is_unknown():
    def failed(_url):
        raise OSError("synthetic lookup failure")
    assert is_probable_fork("owner", "repo", fetch=failed) is None
    for response in ({}, {"fork": "false"}, {"fork": 1}, None, []):
        assert is_probable_fork("owner", "repo", fetch=lambda url: response) is None
    calls = []
    assert is_probable_fork("bad/owner", "repo", fetch=lambda url: calls.append(url)) is None
    assert not calls


def test_runtime_uses_only_standard_library_and_no_transport_import():
    source = ast.parse(Path(sf.__file__).read_text(encoding="utf-8"))
    modules = []
    for node in ast.walk(source):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module)
    assert all(name.split(".")[0] in sys.stdlib_module_names for name in modules)
    assert not any(name in {"socket", "urllib.request", "http.client"} for name in modules)
