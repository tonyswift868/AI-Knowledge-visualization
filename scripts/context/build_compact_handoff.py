"""Build a bounded, local-only repository handoff. Python standard library only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
READ_FIRST = (
    "AGENTS.md", "ARCHITECTURE.md", "docs/project/CURRENT_STATE.md",
    "docs/exec-plans/active/MAIN3.md", "docs/governance/ACTIVE_BOARD.json",
    "docs/governance/DEPENDENCY_QUEUE.json",
)
OUTPUT = "artifacts/handoff/mentorflow-handoff.txt"
WORKERS = {"A", "01", "02", "03", "04", "B"}
STATES = {"IDLE", "RUNNING", "READY", "BLOCKED", "SILENT", "UNKNOWN"}
TASK_STATES = {"READY", "BLOCKED", "NOT_DISPATCHED", "DONE", "UNKNOWN"}
SHA = re.compile(r"[0-9a-f]{40}\Z")
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    re.compile(r"(?i)\bpassword\s*="),
    re.compile(r'(?i)["\']password["\']\s*:\s*["\'][^"\']+'),
    re.compile(r"(?i)Authorization\s*:\s*Bearer\b"),
    re.compile(r'(?i)\b(?:smtp[_-]?(?:key|password)|brevo[_-]?(?:key|api_key)|api[_-]?(?:key|secret)|access[_-]?token)\b["\']?\s*[:=]\s*["\']?[^\s"\',}]+'),
    re.compile(r"\bxkeysib-[A-Za-z0-9_-]+"),
)


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    require(not Path(relative).is_absolute(), "absolute input path forbidden")
    require(path.resolve().is_relative_to(root.resolve()), "path escapes repository")
    for part in [path, *path.parents]:
        if part == root.parent:
            break
        require(not part.is_symlink() and not getattr(part, "is_junction", lambda: False)(),
                "redirected repository path forbidden")
    return path


def read(root: Path, relative: str) -> str:
    path = safe_path(root, relative)
    require(path.is_file() and path.stat().st_size <= 262144, f"missing/oversized input: {relative}")
    return path.read_text(encoding="utf-8-sig")


def secret_count(text: str) -> int:
    return sum(bool(pattern.search(line)) for line in text.splitlines() for pattern in SECRET_PATTERNS)


def no_duplicate_keys(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def text_field(value, *, nullable=False) -> bool:
    return (nullable and value is None) or (
        isinstance(value, str) and 0 < len(value) <= 512
        and all(ord(c) >= 32 and ord(c) != 127 for c in value)
    )


def string_list(value) -> bool:
    return isinstance(value, list) and len(value) == len(set(v for v in value if isinstance(v, str))) and all(text_field(v) for v in value)


def load_state(root: Path):
    current = read(root, READ_FIRST[2])
    fields = dict(re.findall(r"^([A-Z0-9_]+) = ([^\r\n]+)$", current, re.M))
    required = {"PROJECT", "GENERATION", "BASELINE_BRANCH", "MAIN2_CANONICAL_SHA", "MAIN2_CANONICAL_TREE", "SOURCE_BASELINE", "PRODUCT_ACCEPTANCE", "INTEGRATION_BRANCH"}
    require(required <= fields.keys(), "CURRENT_STATE required fields missing")
    for name in required:
        require(len(re.findall(rf"^{name} = ", current, re.M)) == 1 and text_field(fields[name]), "duplicate/invalid current state field")
    require(fields["PROJECT"] == "MentorFlow", "project mismatch")
    require(bool(SHA.fullmatch(fields["MAIN2_CANONICAL_SHA"])) and bool(SHA.fullmatch(fields["MAIN2_CANONICAL_TREE"])), "invalid baseline SHA/tree")
    board = json.loads(read(root, READ_FIRST[4]), object_pairs_hook=no_duplicate_keys)
    queue = json.loads(read(root, READ_FIRST[5]), object_pairs_hook=no_duplicate_keys)
    require(type(board.get("schema_version")) is int and board["schema_version"] == 1, "board schema mismatch")
    require(type(queue.get("schema_version")) is int and queue["schema_version"] == 1, "queue schema mismatch")
    for key, state_key in (("project", "PROJECT"), ("generation", "GENERATION"), ("baseline_branch", "BASELINE_BRANCH"), ("baseline_sha", "MAIN2_CANONICAL_SHA"), ("baseline_tree", "MAIN2_CANONICAL_TREE"), ("integration_branch", "INTEGRATION_BRANCH")):
        require(board.get(key) == fields[state_key], f"state disagreement: {key}")
    require(queue.get("generation") == fields["GENERATION"], "queue generation mismatch")
    require(isinstance(board.get("workers"), dict) and set(board["workers"]) == WORKERS, "worker slots mismatch")
    for worker in board["workers"].values():
        require(isinstance(worker, dict) and {"status", "dispatch_id", "task_id", "depends_on", "unlocks", "delivery_commit", "next_action"} <= worker.keys(), "worker fields missing")
        require(worker["status"] in STATES, "unknown worker status value")
        require(all(text_field(worker[k], nullable=True) for k in ("dispatch_id", "task_id", "delivery_commit")), "invalid worker identity")
        require(worker["delivery_commit"] is None or bool(SHA.fullmatch(worker["delivery_commit"])), "short/invalid delivery SHA")
        require(string_list(worker["depends_on"]) and string_list(worker["unlocks"]) and text_field(worker["next_action"]), "invalid worker dependency/action")
        if worker["status"] in {"READY", "RUNNING"}:
            require(worker["dispatch_id"] is not None and worker["task_id"] is not None, "active worker has no dispatch/task")
    require(isinstance(queue.get("tasks"), list), "task list missing")
    tasks = {}
    for task in queue["tasks"]:
        require(isinstance(task, dict) and {"task_id", "owner", "state", "dependencies", "unlocks", "user_action_required", "reason"} <= task.keys(), "task fields missing")
        require(text_field(task["task_id"]) and task["task_id"] not in tasks, "invalid/duplicate task ID")
        require(task["owner"] in WORKERS | {"UNASSIGNED"} and task["state"] in TASK_STATES, "task owner/state invalid")
        require(string_list(task["dependencies"]) and string_list(task["unlocks"]), "invalid task dependencies")
        require(text_field(task["reason"]) and text_field(task["user_action_required"], nullable=True), "invalid task action/reason")
        tasks[task["task_id"]] = task
    visiting, visited = set(), set()
    def visit(key):
        require(key in tasks and key not in visiting, "missing dependency or dependency cycle")
        if key in visited:
            return
        visiting.add(key)
        for dependency in tasks[key]["dependencies"]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
    for key, task in tasks.items():
        visit(key)
        require(all(k in tasks and key in tasks[k]["dependencies"] for k in task["unlocks"]), "inconsistent unlock edge")
        if task["state"] == "READY":
            require(all(tasks[k]["state"] == "DONE" for k in task["dependencies"]), "READY task has unsatisfied dependencies")
    for worker in board["workers"].values():
        require(all(key in tasks for key in worker["depends_on"] + worker["unlocks"]), "worker dependency absent from queue")
    require(secret_count(current + json.dumps(board) + json.dumps(queue)) == 0, "obvious secret material in knowledge input")
    for path in READ_FIRST:
        read(root, path)
    return fields, board, tasks


def git(root: Path, *args: str, detached=False) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8", timeout=15)
    if detached and result.returncode == 1:
        return "DETACHED"
    require(result.returncode == 0, "local Git read failed")
    return result.stdout.strip()


def build(root: Path, delta: str = "NONE") -> bytes:
    fields, board, tasks = load_state(root)
    require(text_field(delta) and secret_count(delta) == 0, "invalid or sensitive session delta")
    head = git(root, "rev-parse", "HEAD")
    require(bool(SHA.fullmatch(head)), "invalid HEAD")
    require(git(root, "rev-parse", fields["MAIN2_CANONICAL_SHA"] + "^{tree}") == fields["MAIN2_CANONICAL_TREE"], "baseline tree not reproducible")
    git(root, "merge-base", "--is-ancestor", fields["MAIN2_CANONICAL_SHA"], head)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", detached=True)
    rows = [f"PROJECT = {fields['PROJECT']}", f"GENERATION = {fields['GENERATION']}", f"CANONICAL_BRANCH = {fields['BASELINE_BRANCH']}", f"CANONICAL_SHA = {fields['MAIN2_CANONICAL_SHA']}", f"CANONICAL_TREE = {fields['MAIN2_CANONICAL_TREE']}", f"CURRENT_GIT_BRANCH = {branch}", f"CURRENT_GIT_HEAD = {head}", f"PRODUCT_ACCEPTANCE = {fields['PRODUCT_ACCEPTANCE']}", "BOARD = DELIVERY_SNAPSHOT_NOT_LIVE"]
    active = [f"{key}:{w['status']}:{w['task_id']}" for key, w in board["workers"].items() if w["status"] in {"RUNNING", "READY", "BLOCKED"}]
    rows += ["ACTIVE_WORKERS = " + ("; ".join(active) or "NONE"), "UNKNOWN_WORKERS = " + (",".join(k for k, w in board["workers"].items() if w["status"] == "UNKNOWN") or "NONE")]
    for state in ("READY", "BLOCKED", "NOT_DISPATCHED", "UNKNOWN"):
        rows.append(state + "_TASKS = " + (",".join(k for k, t in tasks.items() if t["state"] == state) or "NONE"))
    rows += ["USER_ACTION_REQUIRED ="] + [f"- {key}: {task['user_action_required']}" for key, task in tasks.items() if task["state"] != "DONE" and task["user_action_required"]]
    rows += ["READ_FIRST = " + json.dumps(READ_FIRST), f"SESSION_DELTA = {delta}", "DETAILS = docs/governance/DEPENDENCY_QUEUE.json; unknown does not authorize dispatch."]
    output = ("\n".join(rows) + "\n").encode("utf-8")
    require(len(output) <= 4096, "handoff exceeds 4096 bytes; shorten source snapshot/delta explicitly")
    require(secret_count(output.decode("utf-8")) == 0, "obvious secret material in output")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-delta", default="NONE")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        output = build(ROOT, args.session_delta)
        destination = safe_path(ROOT, OUTPUT)
        if args.check:
            require(destination.is_file() and destination.read_bytes() == output, "handoff absent/stale; regenerate")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(output)
        print(f"HANDOFF_BYTES={len(output)} PATH={OUTPUT} STATUS=PASS")
    except (ValueError, OSError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f"HANDOFF=FAIL ({type(exc).__name__}); validate local state and required fields.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
