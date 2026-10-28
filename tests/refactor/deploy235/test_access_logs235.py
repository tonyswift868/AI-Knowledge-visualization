"""The access counter follows the verified deployed Java logger format."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SPEC = spec_from_file_location("access_counts235", ROOT / "refactor/server-java/deploy/access-counts.py")
access_counts = module_from_spec(SPEC)
SPEC.loader.exec_module(access_counts)


def test_counts_java_profile_events_without_exposing_identifiers():
    lines = [
        '2026-10-28T20:35:00Z INFO RequestAccessLog149Filter - '
        'http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=7',
        '2026-10-28T20:35:01Z INFO RequestAccessLog149Filter - '
        'http method=PUT route=/api/enterprise/account/profile/avatar status=204 elapsed_ms=8',
        '2026-10-28T20:35:02Z INFO RequestAccessLog149Filter - '
        'http method=GET route=UNMATCHED status=404 elapsed_ms=1',
    ]
    counts = access_counts.count_lines(lines)
    assert counts[("GET", "/api/enterprise/account/profile", "200")] == 1
    assert counts[("PUT", "/api/enterprise/account/profile/avatar", "204")] == 1
    assert len(counts) == 2


def test_counts_method_and_status_separately_for_same_profile_route():
    lines = [
        "http method=GET route=/api/enterprise/account/profile status=200 elapsed_ms=3",
        "http method=GET route=/api/enterprise/account/profile status=401 elapsed_ms=2",
    ]
    counts = access_counts.count_lines(lines)
    assert counts[("GET", "/api/enterprise/account/profile", "200")] == 1
    assert counts[("GET", "/api/enterprise/account/profile", "401")] == 1


def test_output_is_only_allow_listed_counts_and_never_echoes_unmatched_line(capsys, monkeypatch):
    import io
    import sys

    rows = [
        "http method=PATCH route=/api/enterprise/account/profile status=200 elapsed_ms=4 query=private@example.invalid token=secret",
        "http method=GET route=UNMATCHED status=404 elapsed_ms=1 email=private@example.invalid token=secret",
    ]
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(rows)))
    assert access_counts.main() == 0
    output = capsys.readouterr().out
    assert "private" not in output and "secret" not in output
    assert output == '{"counts":[{"method":"PATCH","path":"/api/enterprise/account/profile","status":200,"count":1}]}\n'
