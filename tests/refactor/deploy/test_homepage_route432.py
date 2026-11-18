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
    location = /download { return 302 https://github.com/tonyswift868/AI-Knowledge-visualization/releases/latest/download/MentorFlow-Setup.exe; }
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
    assert candidate.count("set $aiteacher_static http://mentorflow-e108-update-static:8081;") == 4
    assert "rewrite ^/$ /mentorflow/updates/e108/site/index.html break;" in candidate
    assert "rewrite ^/download$ /mentorflow/updates/e108/site/download.html break;" in candidate
    assert "rewrite ^/images/(.*)$ /mentorflow/updates/e108/site/images/$1 break;" in candidate
    assert candidate.count("if ($request_method !~ ^(GET|HEAD)$) { return 405; }") == 4
    assert candidate.count("location = /download {") == 1
    assert "location ^~ /updates/app/" in candidate
    assert "location / { return 404; }" in candidate


def test_refuses_multiple_placeholder(tmp_path):
    source = tmp_path / "edge.conf"
    output = tmp_path / "candidate.conf"
    source.write_text(
        BASE.replace(
            "    location = /download",
            "    # Placeholder until the product homepage is built.\n    location = / { return 200 'duplicate'; }\n    location = /download",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        prepare(source, output)
    assert not output.exists()


def test_replaces_download_route_in_existing_homepage_config(tmp_path):
    source = tmp_path / "edge.conf"
    output = tmp_path / "candidate.conf"
    existing = BASE.replace(
        "    # Placeholder until the product homepage is built.\n    location = / {\n        default_type \"text/html; charset=utf-8\";\n        add_header Cache-Control \"no-cache\" always;\n        return 200 '<!doctype html><p>placeholder</p>';\n    }",
        "    location = / { return 200 'homepage'; }\n    location ^~ /images/ { return 404; }",
    )
    source.write_text(existing, encoding="utf-8")

    prepare(source, output)

    candidate = output.read_text(encoding="utf-8")
    assert "location = / { return 200 'homepage'; }" in candidate
    assert "rewrite ^/download$ /mentorflow/updates/e108/site/download.html break;" in candidate
    assert candidate.count("location = /download {") == 1


def test_refuses_existing_images_location(tmp_path):
    source = tmp_path / "edge.conf"
    output = tmp_path / "candidate.conf"
    source.write_text(BASE.replace("    location / { return 404; }", "    location ^~ /images/ { return 404; }\n    location / { return 404; }"), encoding="utf-8")
    with pytest.raises(ValueError):
        prepare(source, output)
    assert not output.exists()


def test_homepage_download_buttons_use_download_route():
    page = (ROOT / "refactor/server-java/deploy/site/index.html").read_text(encoding="utf-8")
    assert page.count('href="/download"') == 2
    assert "updates/app/releases/bootstrap/MentorFlow-Setup.exe" not in page
    assert "github.com/tonyswift868/AI-Knowledge-visualization/releases/latest/download/MentorFlow-Setup.exe" not in page


def test_download_page_probes_github_and_uses_workflow_current_metadata_for_ip_backup():
    page = (ROOT / "refactor/server-java/deploy/site/download.html").read_text(encoding="utf-8")
    assert "fetch('/releases/current.json?ts='" in page
    assert "https://89.45.45.28/releases/" in page
    assert "mode:'no-cors'" in page
    assert "setTimeout(() => reject(new Error('timeout')), 5000)" in page
    assert "Promise.all([meta, probe])" not in page
    assert "current.json?ts=' + Date.now()" in page
    assert "GitHub 下载" in page and "备用线路下载" in page
