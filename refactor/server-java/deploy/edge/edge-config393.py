#!/usr/bin/env python3
"""Apply the two-domain access log directives to an existing nginx config."""
from __future__ import annotations

import re
import sys
from pathlib import Path


DOMAINS = {"auth.clannad.win", "aiteacher.clannad.win"}
FRAGMENT_LINES = Path(__file__).with_name("edge-logging393.conf").read_text(encoding="utf-8").splitlines()
FORMAT = next(line.strip() for line in FRAGMENT_LINES if line.strip().startswith("log_format "))
ACCESS = next(line.strip() for line in FRAGMENT_LINES if line.strip().startswith("access_log "))


def transform(source: str) -> str:
    if "log_format mentorflow_edge" in source:
        raise ValueError("mentorflow_edge format already exists")
    lines = source.splitlines(keepends=True)
    http_opens = [i for i, line in enumerate(lines) if re.match(r"^\s*http\s*\{", line)]
    if len(http_opens) != 1:
        raise ValueError("expected exactly one http block")

    depth = 0
    server_depth: int | None = None
    domain: str | None = None
    found: dict[str, int] = {host: 0 for host in DOMAINS}
    replaced: dict[str, int] = {host: 0 for host in DOMAINS}
    result: list[str] = []
    for line in lines:
        code = line.split("#", 1)[0]
        if server_depth is not None and depth < server_depth:
            server_depth = None
            domain = None
        if re.match(r"^\s*server\s*\{", code):
            server_depth = depth + 1
            domain = None
        match = re.match(r"^\s*server_name\s+([^;]+);", code)
        if server_depth is not None and depth == server_depth and match:
            names = match.group(1).split()
            selected = DOMAINS.intersection(names)
            if selected:
                if len(selected) != 1 or len(names) != 1:
                    raise ValueError("target domain must be the only server_name in its server")
                domain = next(iter(selected))
                found[domain] += 1
        if domain and depth == server_depth and re.match(r"^\s*access_log\s+off\s*;", code):
            replaced[domain] += 1
            line = re.sub(r"access_log\s+off\s*;", ACCESS, line, count=1)
        result.append(line)
        if re.match(r"^\s*http\s*\{", code):
            result.append(f"    {FORMAT}\n")
        depth += code.count("{") - code.count("}")

    if any(found[host] != 1 or replaced[host] != 1 for host in DOMAINS):
        raise ValueError(f"expected one server and one access_log off per target domain: {found}, {replaced}")
    return "".join(result)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "apply":
            raise ValueError("usage: edge-config393.py apply INPUT")
        source = Path(sys.argv[2]).read_text(encoding="utf-8")
        # apply writes a validated candidate to stdout for the shell installer.
        sys.stdout.write(transform(source))
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"edge config assembly failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
