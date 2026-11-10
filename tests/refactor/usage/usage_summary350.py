"""Read-only UsageLog summary for MF-04 350.

The schema and field names mirror refactor/learning/connections/usage.py.
The database is opened with SQLite's ``mode=ro`` URI, and only the calls table
is read.  Date filters are half-open UTC bounds: [--start, --end).
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import sys


TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
)


def _db(path: Path) -> sqlite3.Connection:
    uri = "file:" + path.resolve().as_posix() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db


def _parse_date(value: str | None, *, end: bool = False) -> str | None:
    if value is None:
        return None
    if len(value) == 10:
        value = value + ("T00:00:00+00:00" if not end else "T00:00:00+00:00")
    if not value.endswith("Z") and "+" not in value[10:] and "-" not in value[10:]:
        value += "+00:00"
    # SQLite stores ISO timestamps with an explicit UTC offset in UsageLog.
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _number(value):
    return value if type(value) is int and value >= 0 else 0


def _money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise InvalidOperation
        return amount
    except (InvalidOperation, ValueError, TypeError):
        return None


def summarize(path: Path, start: str | None = None, end: str | None = None):
    start_bound = _parse_date(start)
    end_bound = _parse_date(end, end=True)
    sql = "SELECT provider_id, model, purpose, at_utc, state, usage_json, cost_json FROM calls"
    clauses = []
    params: list[str] = []
    if start_bound:
        clauses.append("at_utc >= ?"); params.append(start_bound)
    if end_bound:
        clauses.append("at_utc < ?"); params.append(end_bound)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY at_utc, provider_id, model, purpose"

    groups = defaultdict(lambda: {
        "calls": 0,
        "states": defaultdict(int),
        "tokens": {field: 0 for field in TOKEN_FIELDS},
        "unknown_tokens": set(),
        "partial_tokens": False,
        "amounts": defaultdict(Decimal),
        "partial_amounts": defaultdict(Decimal),
        "unknown_amounts": 0,
    })
    db = _db(path)
    try:
        for row in db.execute(sql, params):
            # UsageLog at_utc is ISO UTC; the first seven chars are YYYY-MM.
            month = row["at_utc"][:7]
            key = (month, row["provider_id"], row["model"], row["purpose"])
            group = groups[key]
            group["calls"] += 1
            group["states"][row["state"]] += 1

            usage = json.loads(row["usage_json"]) if row["usage_json"] else None
            if usage is None:
                group["unknown_tokens"].update(TOKEN_FIELDS)
            else:
                raw = usage.get("raw") if isinstance(usage.get("raw"), dict) else {}
                meta = raw.get("_mentorflow") if isinstance(raw.get("_mentorflow"), dict) else {}
                missing = set(meta.get("unreported_fields", []))
                for field in TOKEN_FIELDS:
                    if field in missing:
                        group["unknown_tokens"].add(field)
                    else:
                        group["tokens"][field] += _number(usage.get(field))
                if meta.get("partial_usage") is True:
                    group["partial_tokens"] = True

            cost = json.loads(row["cost_json"]) if row["cost_json"] else None
            if isinstance(cost, dict) and _money(cost.get("amount")) is not None:
                target = group["partial_amounts"] if cost.get("estimated") is True else group["amounts"]
                target[cost.get("currency") or "未知"] += _money(cost["amount"])
            else:
                # Keep the absence visible; never turn an unpriced call into 0.
                group["unknown_amounts"] += 1
    finally:
        db.close()
    return groups


def _fmt_tokens(group, field):
    value = str(group["tokens"][field])
    if field in group["unknown_tokens"]:
        return "未知"
    if group["partial_tokens"]:
        return "部分：" + value
    return value


def _fmt_amount(group):
    known = []
    for currency, amount in sorted(group["amounts"].items()):
        known.append(f"{currency} {amount:f}")
    for currency, amount in sorted(group["partial_amounts"].items()):
        known.append(f"部分：{currency} {amount:f}")
    if group["unknown_amounts"]:
        known.append(f"未知（{group['unknown_amounts']} 条）")
    return "；".join(known) if known else "未知"


def render(groups, *, source: Path, start: str | None, end: str | None) -> str:
    lines = [
        "# MentorFlow 本机 UsageLog 汇总（350-MF04）",
        "",
        f"来源副本：`{source}`；读取方式：SQLite `mode=ro`，只读 `calls` 表。",
        "时间口径：按 UsageLog 的 `at_utc` 转换到 UTC 月份；日期参数为半开区间 `[start, end)`。",
        "金额直接取 `cost_json.amount/currency`；缺失金额显示“未知”，不估算。缓存 token 取 `cached_input_tokens`。",
        "",
        "| 月 | 供应商 | 模型 | 用途 | 调用次数 | 输入 token | 输出 token | 缓存 token | 金额 | 状态 |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    if not groups:
        lines.append("| — | — | — | — | 0 | 0 | 0 | 0 | 未知 | — |")
    for (month, provider, model, purpose), group in sorted(groups.items()):
        state = ", ".join(f"{k}={v}" for k, v in sorted(group["states"].items()))
        lines.append(
            f"| {month} | {provider} | {model} | {purpose} | {group['calls']} | "
            f"{_fmt_tokens(group, 'input_tokens')} | {_fmt_tokens(group, 'output_tokens')} | "
            f"{_fmt_tokens(group, 'cached_input_tokens')} | {_fmt_amount(group)} | {state} |"
        )
    lines.extend([
        "",
        "说明：该表只保留按月、供应商、模型、用途的汇总，不输出 request_id、plan_id、错误详情、提示词或原始请求内容。",
    ])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--start", help="UTC 起始日期/时间（含）")
    parser.add_argument("--end", help="UTC 结束日期/时间（不含）")
    parser.add_argument("--output", type=Path, help="写 Markdown；省略则输出到 stdout")
    args = parser.parse_args(argv)
    if not args.database.is_file():
        parser.error(f"UsageLog 不存在：{args.database}")
    result = render(summarize(args.database, args.start, args.end), source=args.database,
                    start=args.start, end=args.end)
    if args.output:
        args.output.write_text(result, encoding="utf-8")
    else:
        sys.stdout.write(result)


if __name__ == "__main__":
    main()
