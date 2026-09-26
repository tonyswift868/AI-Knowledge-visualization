"""Real 44-item vendor response plus clearly identified derived arXiv variants."""
from dataclasses import asdict
import json
from pathlib import Path

from refactor.learning.course.source_filter import RawHit, load_lists, normalize_url, screen


FIXTURES = Path(__file__).with_name("fixtures")


def benchmark_hits():
    raw = json.loads((FIXTURES / "with_tool.json").read_text(encoding="utf-8"))
    content = raw["response"]["content"]
    queries = {block["id"]: block.get("input", {}).get("query", "")
        for block in content if block.get("type") == "server_tool_use"}
    items = [
        RawHit(item.get("url") or "", item.get("title") or "", query=queries.get(block["tool_use_id"], ""))
        for block in content if block.get("type") == "web_search_tool_result"
        for item in block["content"]
    ]
    return items


def benchmark_audit():
    """Rows 42-44 are audit-only discarded rows, not returned ScreenedHits."""
    hits = benchmark_hits()
    result = iter(screen(hits))
    rows = []
    for index, raw in enumerate(hits, 1):
        if normalize_url(raw.url) is None:
            row = dict(url=None, original_url=raw.url, title=raw.title, tier="discarded",
                category="unknown", reasons=["invalid_url"], duplicate_of=None)
        else:
            row = asdict(next(result))
            row["reasons"] = list(row["reasons"])
        rows.append({"row": index, **row})
    return rows


def test_real_sample_count_and_empty_tool_errors_are_dropped():
    hits = benchmark_hits()
    assert len(hits) == 44
    assert sum(not hit.url for hit in hits) == 3
    results = screen(hits)
    assert len(results) == 41
    assert [row.original_url for row in results] == [hit.url for hit in hits if hit.url]
    assert all(row.url for row in results)


def test_real_sample_csdn_fork_and_original():
    results = screen(benchmark_hits())
    csdn = next(row for row in results if "adg.csdn.net" in row.url)
    assert csdn.tier == "low" and "repost_site" in csdn.reasons
    fork = next(row for row in results if "github.com/wshao12/hello-agents" in row.url)
    assert "possible_fork" in fork.reasons and fork.tier == "neutral"
    originals = [row for row in results if "github.com/datawhalechina/hello-agents" in row.url]
    assert originals and all(row.tier == "whitelist" and "possible_fork" not in row.reasons for row in originals)


def test_real_sample_raw_readme_duplicate_and_master_not_assumed_equal():
    results = screen(benchmark_hits())
    first, master, repeated = results[0], results[4], results[5]
    assert first.url == "https://github.com/datawhalechina/hello-agents/blob/main/README_EN.md"
    assert repeated.url == repeated.duplicate_of == first.url
    assert repeated.reasons == ("raw_file", "duplicate")
    assert master.duplicate_of is None and "/blob/master/" in master.url
    unrelated = next(row for row in results if "makr-code/themisdb" in row.url)
    assert unrelated.tier == "neutral" and "raw_file" in unrelated.reasons


def test_real_sample_webvpn_and_official_openai_docs():
    results = screen(benchmark_hits())
    proxy = next(row for row in results if "webvpna.lzu.edu.cn" in row.url)
    assert proxy.tier == "blocked" and "webvpn_proxy" in proxy.reasons
    docs = [row for row in results if "developers.openai.com" in row.url]
    assert len(docs) == 3
    assert all(row.tier == "whitelist" and row.category == "official_docs" for row in docs)


def test_arxiv_forms_derived_from_real_sample_merge_without_conflating_papers():
    originals = [hit for hit in benchmark_hits() if "arxiv.org/" in hit.url]
    assert len(originals) == 4
    # The original 44 have four DIFFERENT papers. Add abs/pdf/export/version
    # forms of their actual IDs to test alias merging, without altering fixture.
    variants = []
    for hit in originals:
        identifier = normalize_url(hit.url).removeprefix("https://arxiv.org/abs/")
        variants.extend([hit, RawHit(f"https://arxiv.org/abs/{identifier}v2"),
            RawHit(f"https://export.arxiv.org/pdf/{identifier}v3.pdf"),
            RawHit(f"http://web3.arxiv.org/pdf/{identifier}")])
    results = screen(variants)
    assert len({hit.url for hit in results}) == 4
    assert sum("duplicate" in hit.reasons for hit in results) == 12
    assert all(hit.url.startswith("https://arxiv.org/abs/") for hit in results)


def test_reviewable_44_row_audit_matches_screening():
    expected = json.loads((FIXTURES / "screened_with_tool.json").read_text(encoding="utf-8"))
    assert benchmark_audit() == expected
    assert len(expected) == 44
    assert all(row["tier"] == "discarded" for row in expected[41:])


def test_pending_domains_are_not_accidentally_whitelisted_by_parent_domain():
    lists = load_lists()
    results = screen((RawHit("https://" + rule.match + "/") for rule in lists.candidates), lists=lists)
    assert all(result.tier != "whitelist" for result in results)
