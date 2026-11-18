#!/usr/bin/env python3
"""Prepare the reviewed nginx candidate for the aiteacher homepage."""
from __future__ import annotations

import argparse
from pathlib import Path
import re


UPSTREAM = "http://mentorflow-e108-update-static:8081"
PLACEHOLDER = re.compile(
    r"(?ms)^[ \t]*# Placeholder until the product homepage is built\.\r?\n"
    r"[ \t]*location\s*=\s*/\s*\{.*?^[ \t]*\}"
)
IMAGES = re.compile(r"(?m)^[ \t]*location\s*\^~\s+/images/\s*\{")
DOWNLOAD = re.compile(r"(?ms)^[ \t]*location\s*=\s*/download\s*\{.*?^[ \t]*\}")

HOMEPAGE = f'''        location = / {{
            if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
            add_header Cache-Control "no-cache" always;
            set $aiteacher_static {UPSTREAM};
            rewrite ^/$ /mentorflow/updates/e108/site/index.html break;
            proxy_pass $aiteacher_static;
            proxy_buffering off;
            proxy_read_timeout 300s;
        }}
        location ^~ /images/ {{
            if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
            set $aiteacher_static {UPSTREAM};
            rewrite ^/images/(.*)$ /mentorflow/updates/e108/site/images/$1 break;
            proxy_pass $aiteacher_static;
            proxy_buffering off;
            proxy_read_timeout 300s;
        }}'''


def _read(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def prepare(source: Path, destination: Path) -> None:
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("candidate output must not overwrite the source config")
    text = _read(source)
    placeholders = list(PLACEHOLDER.finditer(text))
    if len(placeholders) != 1:
        raise ValueError(
            f"expected exactly one homepage placeholder block; found {len(placeholders)}"
        )
    if IMAGES.search(text):
        raise ValueError("refusing to add a duplicate /images/ location")
    match = placeholders[0]
    downloads = list(DOWNLOAD.finditer(text))
    if len(downloads) != 1:
        raise ValueError(f"expected exactly one /download location; found {len(downloads)}")
    replacement = HOMEPAGE
    candidate = text[: match.start()] + replacement + text[match.end() :]
    download_block = f'''        location = /download {{
            if ($request_method !~ ^(GET|HEAD)$) {{ return 405; }}
            add_header Cache-Control "no-cache" always;
            set $aiteacher_static {UPSTREAM};
            rewrite ^/download$ /mentorflow/updates/e108/site/download.html break;
            proxy_pass $aiteacher_static;
            proxy_buffering off;
            proxy_read_timeout 30s;
        }}'''
    candidate = DOWNLOAD.sub(download_block, candidate, count=1)
    _write(destination, candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.config, args.output)
    print(f"Prepared candidate only: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
