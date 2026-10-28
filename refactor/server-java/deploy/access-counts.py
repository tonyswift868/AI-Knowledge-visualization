#!/usr/bin/env python3
"""Count allow-listed Java account-profile access events from a piped container log."""
from __future__ import annotations

from collections import Counter
import json
import re
import sys


PROFILE_ROUTES = frozenset({
    "/api/enterprise/account/profile",
    "/api/enterprise/account/profile/avatar",
})
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
JAVA_ACCESS = re.compile(
    r"\bhttp method=(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) "
    r"route=(\S+) status=([1-5][0-9]{2})\b"
)


def _candidate(line: str):
    """Accept only RequestAccessLog149Filter's verified SLF4J format."""
    match = JAVA_ACCESS.search(line)
    if not match:
        return None
    method, path, status = match.groups()
    if path not in PROFILE_ROUTES:
        return None
    return method, path, status


def count_lines(lines) -> Counter:
    counts = Counter()
    for line in lines:
        event = _candidate(line)
        if event is not None:
            counts[event] += 1
    return counts


def main() -> int:
    counts = count_lines(sys.stdin)
    rows = [
        {"method": method, "path": path, "status": int(status), "count": count}
        for (method, path, status), count in sorted(counts.items())
    ]
    print(json.dumps({"counts": rows}, ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
