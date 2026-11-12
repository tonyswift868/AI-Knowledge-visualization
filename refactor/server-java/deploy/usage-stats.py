#!/usr/bin/env python3
"""Read-only aggregate statistics for RequestAccessLog149Filter output.

Input is a Java container log on stdin.  The script emits only aggregate data:
no original log line, URI parameters, IP address, or identity is copied out.
Latency percentiles use ``statistics.quantiles(..., method='inclusive')``;
single-sample groups use that sample for every percentile.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import argparse
import json
import os
import re
import statistics
import subprocess
import sys


METHOD_RE = r"GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"
ACCESS_RE = re.compile(
    rf"http method=(?P<method>{METHOD_RE})\s+route=(?P<route>\S+)\s+"
    r"status=(?P<status>[1-5][0-9]{2})\s+elapsed_ms=(?P<elapsed>[0-9]+)\b"
)
ACCESS_NO_LATENCY_RE = re.compile(
    rf"http method=(?P<method>{METHOD_RE})\s+route=(?P<route>\S+)\s+"
    r"status=(?P<status>[1-5][0-9]{2})\b"
)
TIMESTAMP_RE = re.compile(
    r"(?P<stamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
RESTART_RE = re.compile(r"\b(?:restarting|restart|started|stopping|shutdown)\b", re.I)

STATIC_ROUTES = frozenset({
    "/health/live", "/health/ready", "/health/startup",
    "/api/enterprise/account/workspaces",
    "/api/enterprise/account/workspaces/initialize",
    "/api/enterprise/account/profile", "/api/enterprise/account/profile/avatar",
    "/api/enterprise/auth/native/login", "/api/enterprise/auth/native/refresh",
    "/api/enterprise/auth/native/logout", "/api/enterprise/auth/native/session",
    "/api/enterprise/auth/me", "/api/enterprise/auth/admin/overview",
    "/api/enterprise/catalog/source-whitelist", "/api/enterprise/catalog/model-prices",
    "/api/enterprise/auth/lifecycle/capabilities", "/api/enterprise/auth/lifecycle/register",
    "/api/enterprise/auth/lifecycle/register/check", "/api/enterprise/auth/lifecycle/register/verify",
    "/api/enterprise/auth/lifecycle/register/resend", "/api/enterprise/auth/lifecycle/forgot",
    "/api/enterprise/auth/reset/watch", "/api/enterprise/auth/reset/status",
    "/api/enterprise/sync/v1/snapshot", "/api/enterprise/sync/v1/changes",
    "/api/enterprise/sync/v1/entities/bulk", "/api/enterprise/account/closure/prepare",
})
RAG_PREFIX = "/api/enterprise/rag/v1"
SYNC_RE = re.compile(r"^/api/enterprise/sync/v1/entities/[^/]+/[^/]+(?P<delete>/delete)?$")
CLOSURE_RE = re.compile(r"^/api/enterprise/account/closure/(?:prepare|[^/]+(?:/commit)?)$")


def _timestamp(line: str) -> datetime | None:
    match = TIMESTAMP_RE.search(line)
    if not match:
        return None
    value = match.group("stamp").replace(",", ".")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def safe_route(route: str) -> str:
    """Return a known template, collapsing dynamic path components."""
    if "?" in route or "#" in route or not route.startswith("/"):
        return "UNMATCHED"
    if route in STATIC_ROUTES:
        return route
    if route.startswith(RAG_PREFIX + "/documents/"):
        suffix = route[len(RAG_PREFIX + "/documents/"):].strip("/")
        if suffix.endswith("/chunks") and suffix.count("/") == 1:
            return RAG_PREFIX + "/documents/{doc_id}/chunks"
        if suffix and "/" not in suffix:
            return RAG_PREFIX + "/documents/{doc_id}"
        return "UNMATCHED"
    if route == RAG_PREFIX + "/documents" or route == RAG_PREFIX + "/search":
        return route
    sync = SYNC_RE.fullmatch(route)
    if sync:
        return "/api/enterprise/sync/v1/entities/{kind}/{entity_id}" + ("/delete" if sync.group("delete") else "")
    closure = CLOSURE_RE.fullmatch(route)
    if closure:
        if route.endswith("/commit"):
            return "/api/enterprise/account/closure/{operation_id}/commit"
        return "/api/enterprise/account/closure/{operation_id}"
    # A mapped Spring route is already a template. Keep only safe template chars.
    if re.fullmatch(r"/[A-Za-z0-9_{}./-]+", route) and ("{" in route or route in STATIC_ROUTES):
        return route
    return "UNMATCHED"


def _percentiles(values: list[int]) -> dict[str, int | float]:
    if not values:
        return {}
    if len(values) == 1:
        value = values[0]
        return {key: value for key in ("p50", "p95", "p99", "max")}
    ordered = sorted(values)
    quantiles = statistics.quantiles(ordered, n=100, method="inclusive")
    result = {key: quantiles[index - 1] for key, index in (("p50", 50), ("p95", 95), ("p99", 99))}
    result["max"] = max(ordered)
    return {key: int(value) if float(value).is_integer() else value for key, value in result.items()}


def parse_line(line: str) -> dict | None:
    match = ACCESS_RE.search(line) or ACCESS_NO_LATENCY_RE.search(line)
    if not match:
        return None
    stamp = _timestamp(line)
    if stamp is None:
        return None
    route = safe_route(match.group("route"))
    elapsed = int(match.groupdict().get("elapsed") or 0)
    return {"timestamp": stamp, "method": match.group("method"), "route": route,
            "status": int(match.group("status")), "elapsed_ms": elapsed,
            "has_latency": bool(match.groupdict().get("elapsed"))}


def _row(records: list[dict], *, day: str, route: str | None = None) -> dict:
    subset = [r for r in records if r["timestamp"].date().isoformat() == day and (route is None or r["route"] == route)]
    statuses = Counter(r["status"] // 100 for r in subset)
    row = {"day": day}
    if route is not None:
        row["route"] = route
    row.update({"requests": len(subset), "2xx": statuses[2], "3xx": statuses[3],
                "4xx": statuses[4], "5xx": statuses[5]})
    latencies = [r["elapsed_ms"] for r in subset if r["has_latency"]]
    row["latency_ms"] = _percentiles(latencies)
    return row


NO_DATA_SOURCE = "无数据来源"
ACTIVE_ACCOUNTS_BASIS = (
    "sync-active only: public.account_daily_activity.day in UTC, "
    "COUNT(DISTINCT account_scope)"
)


def active_accounts_from_rows(rows, start: date | None = None, end: date | None = None) -> list[dict]:
    """Keep only date/count pairs; never accept or emit an account identifier."""
    result = []
    for raw in rows:
        if isinstance(raw, dict):
            raw_day, raw_count = raw.get("day"), raw.get("accounts", raw.get("count"))
        elif isinstance(raw, (tuple, list)) and len(raw) == 2:
            raw_day, raw_count = raw
        else:
            parts = str(raw).strip().split("\t")
            if len(parts) != 2:
                continue
            raw_day, raw_count = parts
        try:
            day = date.fromisoformat(str(raw_day))
            count = int(str(raw_count))
        except (TypeError, ValueError):
            continue
        if count < 0 or (start and day < start) or (end and day > end):
            continue
        result.append({"day": day.isoformat(), "accounts": count})
    return sorted(result, key=lambda row: row["day"])


def _psql_database_url(value: str) -> str:
    """Accept the project's SQLAlchemy URL without ever printing credentials."""
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value[len("postgresql+psycopg://"):]
    if value.startswith("postgresql://"):
        return value
    raise ValueError("database URL must use postgresql://")


def active_accounts_query(start: date | None = None, end: date | None = None) -> str:
    """Return the fixed, date-bounded SELECT used by the read-only wrapper."""
    if start and end and start > end:
        raise ValueError("start date must not be after end date")
    clauses = []
    if start:
        clauses.append(f"day >= DATE '{start.isoformat()}'")
    if end:
        clauses.append(f"day <= DATE '{end.isoformat()}'")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return (
        "SELECT day::text AS day, "
        "COUNT(DISTINCT account_scope)::text AS accounts "
        "FROM public.account_daily_activity" + where +
        " GROUP BY 1 ORDER BY 1"
    )


def active_accounts_from_psql(db_url: str, start: date | None = None, end: date | None = None,
                              *, psql: str = "psql") -> list[dict] | str:
    """Run the one allowed SELECT in a read-only psql session.

    Dates are validated by ``date.fromisoformat`` before interpolation, and
    the subprocess receives no stdin so a piped access log cannot be consumed
    by psql.  A pre-0050 database reports an explicit unavailable source.
    """
    query = active_accounts_query(start, end)
    env = os.environ.copy()
    # This is intentionally replaced, not appended: every transaction opened
    # by psql must reject INSERT/UPDATE/DELETE even if the caller had looser
    # PGOPTIONS in its environment.
    env["PGOPTIONS"] = "-c default_transaction_read_only=on"
    try:
        completed = subprocess.run(
            [psql, "-XqAt", "-v", "ON_ERROR_STOP=1", "-d", _psql_database_url(db_url), "-c", query],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env, check=False,
        )
    except OSError as exc:
        raise RuntimeError("read-only active-account SELECT could not start psql") from exc
    if completed.returncode:
        if "does not exist" in completed.stderr.lower():
            return NO_DATA_SOURCE
        raise RuntimeError("read-only active-account SELECT failed")
    return active_accounts_from_rows(completed.stdout.splitlines(), start, end)


def summarize_lines(lines, start: date | None = None, end: date | None = None,
                   active_accounts: list[dict] | None = None) -> dict:
    records: list[dict] = []
    ignored = 0
    restart_markers: list[datetime] = []
    for line in lines:
        record = parse_line(line)
        if record is None:
            ignored += 1
            marker = _timestamp(line)
            if marker and RESTART_RE.search(line):
                restart_markers.append(marker)
            continue
        day = record["timestamp"].date()
        if (start and day < start) or (end and day > end):
            continue
        records.append(record)
    records.sort(key=lambda item: item["timestamp"])
    days = sorted({r["timestamp"].date().isoformat() for r in records})
    routes = sorted({r["route"] for r in records})
    request_rows = [_row(records, day=day, route=route) for day in days for route in routes
                    if any(r["timestamp"].date().isoformat() == day and r["route"] == route for r in records)]
    rag_rows = [row for row in request_rows if RAG_PREFIX in row.get("route", "")]
    minute_counts = Counter(r["timestamp"].replace(second=0, microsecond=0) for r in records)
    peak = None
    if minute_counts:
        minute, count = max(minute_counts.items(), key=lambda pair: (pair[1], pair[0]))
        peak = {"minute": minute.isoformat(), "requests": count}
    gaps = []
    for marker in sorted(restart_markers):
        before = max((r["timestamp"] for r in records if r["timestamp"] < marker), default=None)
        after = min((r["timestamp"] for r in records if r["timestamp"] > marker), default=None)
        if before and after:
            gaps.append({"after": before.isoformat(), "before": after.isoformat(), "marker": marker.isoformat()})
    return {
        "log_range": {"first": records[0]["timestamp"].isoformat() if records else None,
                      "last": records[-1]["timestamp"].isoformat() if records else None},
        "requests": request_rows,
        "rag_requests": rag_rows,
        "active_accounts": (None if active_accounts is None
                            else NO_DATA_SOURCE if active_accounts == NO_DATA_SOURCE
                            else active_accounts_from_rows(active_accounts, start, end)),
        "active_accounts_source": ("not_requested" if active_accounts is None
                                    else NO_DATA_SOURCE if active_accounts == NO_DATA_SOURCE
                                    else "read_only_daily_activity"),
        "active_accounts_basis": ACTIVE_ACCOUNTS_BASIS,
        "peak_minute": peak,
        "restart_gaps": gaps,
        "ignored_lines": ignored,
        "latency_recorded": any(r["has_latency"] for r in records),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--db-url", help="read-only PostgreSQL URL; credentials stay in the caller's environment")
    parser.add_argument("--db-url-env", help="environment variable containing a read-only PostgreSQL URL")
    parser.add_argument("--active-accounts-json", help="validated day/count JSON from the read-only shell wrapper")
    parser.add_argument("--psql", default="psql", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.start and args.end and args.start > args.end:
        parser.error("--start must not be after --end")
    if args.db_url and args.db_url_env:
        parser.error("use only one of --db-url and --db-url-env")
    raw_active = None
    if args.active_accounts_json:
        try:
            raw_active = json.loads(args.active_accounts_json)
        except json.JSONDecodeError:
            parser.error("--active-accounts-json must be valid JSON")
        if isinstance(raw_active, dict) and raw_active.get("status") == NO_DATA_SOURCE:
            raw_active = NO_DATA_SOURCE
        elif not isinstance(raw_active, list):
            parser.error("--active-accounts-json must be a list or unavailable marker")
    db_url = args.db_url or (os.environ.get(args.db_url_env) if args.db_url_env else None)
    if args.db_url_env and not db_url:
        parser.error(f"database URL environment variable is missing: {args.db_url_env}")
    if db_url:
        try:
            raw_active = active_accounts_from_psql(db_url, args.start, args.end, psql=args.psql)
        except (RuntimeError, ValueError) as exc:
            parser.error(str(exc))
    print(json.dumps(summarize_lines(sys.stdin, args.start, args.end, raw_active),
                     ensure_ascii=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
