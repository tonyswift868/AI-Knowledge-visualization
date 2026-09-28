"""Batch 7 reuses the batch 6 driver and fixed materials; opt-in paid work only.

All output stays under its designated D: test root. No product orchestrator is
patched: PlanFlow outlines/formal runs and component demonstrations are separate.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

import plan_parameter_eval as base

BASELINE = "cdc1e0b"
NAMES = {"base", "advanced", "practice", "principle"}
DEFAULT_PREVIOUS = base.TEMP_ROOT / "mf04-0928-a"


def configure_retest():
    # Reuse the existing cost guard; these settings apply only to this process.
    base.BASELINE = BASELINE
    base.LIMIT, base.STOP_AT = Decimal("8"), Decimal("7.50")
    base.FORMAL = set(NAMES)
    base.COMPARISONS = tuple(c for c in base.COMPARISONS if c[0] in
                             {"level", "notes_practice", "notes_principle", "notes_pair"})


def isolated(path):
    path = Path(path).resolve()
    if path == base.TEMP_ROOT.resolve() or not path.is_relative_to(base.TEMP_ROOT.resolve()):
        raise ValueError("Use an isolated subdirectory of the designated D: eval-plan root.")
    return path


def prepare_retest(root, previous, retry_failed=False):
    root, previous = isolated(root), isolated(previous)
    if root == previous or root.is_relative_to(previous) or previous.is_relative_to(root):
        raise ValueError("The retest must not overwrite prior evidence.")
    old = base.read_json(previous / "manifest.json")
    fresh = not (root / "manifest.json").exists()
    exp = base.Experiment(root, retry_failed=retry_failed, outline_blind=True)
    prompt_sha = hashlib.sha256((base.REPO / "refactor/learning/course/planner_agents.py").read_bytes()).hexdigest()
    if fresh:
        exp.manifest.update(prefix="mf04p7-" + exp.manifest["prefix"].split("-")[-1],
                            configurations={k: v for k, v in old["configurations"].items() if k in NAMES},
                            today=old["today"], run_order=[v for v in old["run_order"] if v[0] in NAMES],
                            sources=old["sources"], model=old["model"], previous=str(previous),
                            prompt_sha=prompt_sha, protocol_version=7)
        base.write_json(root / "manifest.json", exp.manifest)
        shutil.copytree(previous / "material-snapshot", root / "material-snapshot", dirs_exist_ok=True)
    if exp.manifest.get("prompt_sha") != prompt_sha or exp.manifest.get("previous") != str(previous):
        raise ValueError("Changed prompts or baseline materials require a new experiment root.")
    # A cache miss must fail, not silently fetch changed remote material.
    snapshot = exp.fetch

    def replay_only(url, cancel):
        digest = hashlib.sha256(url.encode()).hexdigest()
        if not (snapshot.root / (digest + ".body")).exists() or not (snapshot.root / (digest + ".json")).exists():
            raise RuntimeError("Missing baseline material snapshot; network replay is disabled.")
        return snapshot(url, cancel)

    exp.fetch = replay_only
    return exp


def failed_module_input(previous):
    """Read the old experiment database in read-only mode, never the real account DB."""
    from refactor.learning.app.plan_flow import PlanFlow
    previous = isolated(previous)
    old = base.read_json(previous / "runs/base-r2/result.json")
    modules = [m for s in old["draft"]["outline"]["stages"] for m in s["modules"]]
    failed = [m for m in modules if old["draft"].get("generation", {}).get(m["id"]) == "failed"]
    if len(failed) != 1:
        raise ValueError("Expected the single saved failed module, do not guess.")
    paths = list((previous / "runs/base-r2/data").rglob("course.sqlite3"))
    if len(paths) != 1:
        raise ValueError("Expected one isolated experiment database.")
    with sqlite3.connect(paths[0].as_uri() + "?mode=ro", uri=True) as db:
        pid = old["draft"]["plan_id"]
        chunks = [json.loads(row[0]) for row in db.execute("SELECT payload FROM source_chunks WHERE plan_key=?", (pid,))]
        sources = [json.loads(row[0]) for row in db.execute("SELECT payload FROM sources WHERE plan_key=?", (pid,))]
    selected = PlanFlow._span_chunks(failed[0], chunks)
    titles = {s["id"]: s.get("title") or s.get("url") for s in sources}
    # Reproduce the EXACT material selection of the failed old request first.
    request_chunks = PlanFlow._module_chunks(selected, titles)
    return old, failed[0], request_chunks


def offline_batch_demo(root, previous):
    """Marked fake generator: verifies citation, splitting, merging and time contracts, NOT model quality."""
    from refactor.learning.course import planner_agents as agents
    root = isolated(root)
    old, module, chunks = failed_module_input(previous)
    batches = agents.split_module_chunks(chunks)
    ranges = agents.batch_minute_ranges(batches, module["minutes"])
    raw_outputs, parsed = [], []
    for index, (batch, minute_range) in enumerate(zip(batches, ranges), 1):
        request = agents.generate_request(old["draft"]["form"], old["draft"]["goal"], old["draft"]["outline"],
            module, batch, minutes_range=minute_range, batch_index=index, batch_count=len(batches), max_points=6)
        target = sum(minute_range) // 2
        count = max(1, (target + 59) // 60)
        aliases = list(request[2])
        points = [dict(title=f"批{index}第{n + 1}个协议测试点", summary=f"第{index}批第{n + 1}点的替身输出，仅验证接线和资料引用；不作为学习内容。",
                       chunks=[aliases[min(n, len(aliases) - 1)]], minutes=target // count + (n < target % count))
                  for n in range(count)]
        payload = json.dumps(dict(points=points), ensure_ascii=False)
        parsed.append(agents.parse_points(payload, request[2], minutes_range=minute_range, min_points=1, max_points=6))
        raw_outputs.append(dict(index=index, chars=sum(len(c["text"]) for c in batch), minutes_range=minute_range,
                                system=request[0], messages=request[1], raw=payload))
    merged = agents.merge_point_batches(parsed, chunks, minutes_range=module["minutes"])
    record = dict(kind="offline fake generation; no model calls; no quality claim", module=module["title"],
                  source_chars=sum(len(c["text"]) for c in chunks), batch_cap=agents.DEFAULT_BATCH_CHARS,
                  batches=raw_outputs, points=merged, minutes=sum(p["minutes"] for p in merged),
                  original_minutes=module["minutes"], network_calls=0)
    base.write_json(root / "offline-batch-demo.json", record)
    print(json.dumps({k: v for k, v in record.items() if k not in ("batches", "points")}, ensure_ascii=False), flush=True)
    return record


def live_batch_demo(exp, previous):
    """Optional real component check, separate from PlanFlow and the offline fake."""
    from refactor.learning.course import planner_agents as agents
    old, module, chunks = failed_module_input(previous)
    batches = agents.split_module_chunks(chunks)
    ranges = agents.batch_minute_ranges(batches, module["minutes"])
    folder = exp.root / "live-batch-demo"
    parsed = []
    for index, (batch, minute_range) in enumerate(zip(batches, ranges), 1):
        path = folder / f"batch-{index}.json"
        prior = base.read_json(path) if path.exists() else {}
        if prior.get("points"):
            parsed.append(prior["points"])
            continue
        if prior and not exp.retry_failed:
            continue
        if prior:
            base.write_json(folder / "failures" / f"batch-{index}-{base.uuid.uuid4().hex[:8]}.json", prior)
        request = agents.generate_request(old["draft"]["form"], old["draft"]["goal"], old["draft"]["outline"], module,
            batch, minutes_range=minute_range, batch_index=index, batch_count=len(batches), max_points=6)
        record = dict(index=index, chars=sum(len(c["text"]) for c in batch), minutes_range=minute_range,
                      system=request[0], messages=request[1], aliases=request[2])
        try:
            result = exp.budget.complete("generate_batch", request[1], run_id=f"component-batch-{index}",
                                         system=request[0], max_tokens=8192, cancel=exp.cancel)
            record["raw"] = result.text
            points = agents.parse_points(result.text, request[2], minutes_range=minute_range, min_points=1, max_points=6)
            record["points"] = points
            parsed.append(points)
        except base.BudgetStop:
            raise
        except Exception as exc:
            record["error_code"] = getattr(exc, "code", type(exc).__name__)
            if isinstance(exc, agents.AgentFormatError):
                record["error_detail"] = str(exc)
        base.write_json(path, record)
        print(json.dumps(dict(event="component_batch", index=index, points=len(record.get("points", [])),
                              error=record.get("error_code"))), flush=True)
    outcome = dict(kind="real model component check; not product orchestration", module=module["title"],
                   expected_batches=len(batches), completed_batches=len(parsed), success=False,
                   source_chars=sum(len(c["text"]) for c in chunks), original_minutes=module["minutes"])
    if len(parsed) == len(batches):
        try:
            outcome["points"] = agents.merge_point_batches(parsed, chunks, minutes_range=module["minutes"])
            outcome["minutes"] = sum(p["minutes"] for p in outcome["points"])
            outcome["success"] = True
        except agents.AgentFormatError as exc:
            outcome["error"] = str(exc)
    base.write_json(folder / "result.json", outcome)
    return outcome


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv and os.environ.get("MF_LIVE_EVAL") != "1":
        print("SKIPPED: real model retest requires MF_LIVE_EVAL=1.")
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--previous", type=Path, default=DEFAULT_PREVIOUS)
    parser.add_argument("--stage", choices=("outlines", "formal", "judge", "demo", "live-batch"), default="outlines")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(base.REPO))
    if args.stage == "demo":
        offline_batch_demo(args.root, args.previous)
        return 0
    if os.environ.get("MF_LIVE_EVAL") != "1":
        print("SKIPPED: real model retest requires MF_LIVE_EVAL=1.")
        return 0
    configure_retest()
    exp = prepare_retest(args.root, args.previous, args.retry_failed)
    try:
        exp.prepare()
        if args.stage == "live-batch":
            return 0 if live_batch_demo(exp, args.previous)["success"] else 3
        elif args.stage == "judge":
            exp.judge()
        else:
            for name, repeat in exp.manifest["run_order"]:
                if args.stage == "outlines" or name in base.FORMAL:
                    exp.run_one(name, repeat, generate=args.stage == "formal")
    except base.BudgetStop as exc:
        base.write_json(exp.root / "budget-stop.json", dict(at=base.now(), message=str(exc)))
        return 2
    finally:
        exp.close()
    runs = [base.read_json(p) for p in (exp.root / "runs").glob("*/result.json")]
    judges = [base.read_json(p) for p in (exp.root / "outline-judges").glob("*.json")]
    status = dict(outlines=sum(r["outline_complete"] for r in runs), formal=sum(r["formal_complete"] for r in runs),
                  judges=sum("selected" in j for j in judges))
    base.write_json(exp.root / "status.json", status)
    expected = dict(outlines=8, formal=8, judge=16)
    return 0 if status["judges" if args.stage == "judge" else args.stage] == expected[args.stage] else 3


if __name__ == "__main__":
    raise SystemExit(main())
