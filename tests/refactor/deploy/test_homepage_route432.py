from __future__ import annotations

import runpy
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[3]
prepare = runpy.run_path(
    str(ROOT / "refactor/server-java/deploy/prepare_homepage_route.py")
)["prepare"]


BASE = """server {
    server_name aiteacher.clannad.win;
    # Placeholder until the product homepage is built.
    location = / {
        default_type \"text/html; charset=utf-8\";
        add_header Cache-Control \"no-cache\" always;
        return 200 '<!doctype html><p>placeholder</p>';
    }
    location = /download { return 302 https://aiteacher.clannad.win/updates/app/releases/bootstrap/MentorFlow-Setup.exe; }
    location ^~ /updates/app/ {
        if ($request_method !~ ^(GET|HEAD)$) { return 405; }
        set $aiteacher_static http://mentorflow-e108-update-static:8081;
        rewrite ^/updates/app/(.*)$ /mentorflow/updates/app/$1 break;
        proxy_pass $aiteacher_static;
    }
    location / { return 404; }
}
"""


def test_replaces_placeholder_and_adds_get_head_routes(tmp_path):
    source = tmp_path / "edge.conf"
    output = tmp_path / "candidate.conf"
    source.write_text(BASE, encoding="utf-8")
    before = source.read_bytes()

    prepare(source, output)

    candidate = output.read_text(encoding="utf-8")
    assert source.read_bytes() == before
    assert "Placeholder until the product homepage is built" not in candidate
    assert "return 200 '<!doctype html>" not in candidate
    assert candidate.count("location = / {") == 1
    assert candidate.count("location ^~ /images/ {") == 1
    assert candidate.count("set $aiteacher_static http://mentorflow-e108-update-static:8081;") == 3
    assert "rewrite ^/$ /mentorflow/updates/e108/site/index.html break;" in candidate
    assert "rewrite ^/images/(.*)$ /mentorflow/updates/e108/site/images/$1 break;" in candidate
    assert candidate.count("if ($request_method !~ ^(GET|HEAD)$) { return 405; }") == 3
    assert "location ^~ /updates/app/" in candidate
    assert "location / { return 404; }" in candidate


@pytest.mark.parametrize(
    "bad",
    [
        BASE.replace("# Placeholder until the product homepage is built.\n", ""),
        BASE.replace(
            "    location = /download",
            "    # Placeholder until the product homepage is built.\n    location = / { return 200 'duplicate'; }\n    location = /download",
        ),
    ],
)
def test_refuses_missing_or_multiple_placeholder(tmp_path, bad):
    source = tmp_path / "edge.conf"
    output = tmp_path / "candidate.conf"
    source.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        prepare(source, output)
    assert not output.exists()


def test_refuses_existing_images_location(tmp_path):
    source = tmp_path / "edge.conf"
    output = tmp_path / "candidate.conf"
    source.write_text(BASE.replace("    location / { return 404; }", "    location ^~ /images/ { return 404; }\n    location / { return 404; }"), encoding="utf-8")
    with pytest.raises(ValueError):
        prepare(source, output)
    assert not output.exists()
