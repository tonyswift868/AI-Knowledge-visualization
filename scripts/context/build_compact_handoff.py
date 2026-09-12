"""Build a bounded, local-only repository handoff. Python standard library only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
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
WEB_OUTPUT = "docs/handoff/WEB_SESSION_LATEST.md"
REPOSITORY_URL = "https://github.com/tonyswift868/AI-Knowledge-visualization"
WEB_INPUTS = (*READ_FIRST, "docs/handoff/README.md", "docs/governance/HANDOFF_CONTRACT.md",
              "docs/PRODUCT_FLOW.md", "docs/project/COMPONENT_MAP.md", "docs/decisions/index.md",
              "scripts/context/build_compact_handoff.py")
SOURCE_PATHS = (".", f":(exclude){WEB_OUTPUT}")
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
    required = {"PROJECT", "GENERATION", "BASELINE_BRANCH", "MAIN2_CANONICAL_SHA", "MAIN2_CANONICAL_TREE", "SOURCE_BASELINE", "PRODUCT_ACCEPTANCE", "INTEGRATION_BRANCH", "KNOWLEDGE_COMMIT", "KNOWLEDGE_TREE"}
    require(required <= fields.keys(), "CURRENT_STATE required fields missing")
    for name in required:
        require(len(re.findall(rf"^{name} = ", current, re.M)) == 1 and text_field(fields[name]), "duplicate/invalid current state field")
    require(fields["PROJECT"] == "MentorFlow", "project mismatch")
    require(bool(SHA.fullmatch(fields["MAIN2_CANONICAL_SHA"])) and bool(SHA.fullmatch(fields["MAIN2_CANONICAL_TREE"])), "invalid baseline SHA/tree")
    board = json.loads(read(root, READ_FIRST[4]), object_pairs_hook=no_duplicate_keys)
    queue = json.loads(read(root, READ_FIRST[5]), object_pairs_hook=no_duplicate_keys)
    require(isinstance(board, dict) and isinstance(queue, dict), "state must be JSON objects")
    require(type(board.get("schema_version")) is int and board["schema_version"] == 1, "board schema mismatch")
    require(type(queue.get("schema_version")) is int and queue["schema_version"] == 1, "queue schema mismatch")
    for key, state_key in (("project", "PROJECT"), ("generation", "GENERATION"), ("baseline_branch", "BASELINE_BRANCH"), ("baseline_sha", "MAIN2_CANONICAL_SHA"), ("baseline_tree", "MAIN2_CANONICAL_TREE"), ("integration_branch", "INTEGRATION_BRANCH")):
        require(board.get(key) == fields[state_key], f"state disagreement: {key}")
    require(queue.get("generation") == fields["GENERATION"], "queue generation mismatch")
    require(board.get("repository_url") == REPOSITORY_URL, "repository identity mismatch")
    knowledge = board.get("knowledge_delivery")
    require(isinstance(knowledge, dict), "knowledge identity missing")
    for key, state_key in (("commit", "KNOWLEDGE_COMMIT"), ("tree", "KNOWLEDGE_TREE")):
        require(bool(SHA.fullmatch(fields[state_key])) and knowledge.get(key) == fields[state_key], "knowledge identity disagreement")
    require(knowledge.get("ref") == knowledge["commit"], "knowledge ref must pin exact commit")
    control = board.get("control_plane")
    require(isinstance(control, dict), "control-plane snapshot missing")
    require(control.get("active_slots") == ["A", "01", "02", "03", "04"], "active slot scope mismatch")
    for key in ("repository_setup", "branch_picker", "a_status_at_dispatch", "a_isolation",
                "worker_dispatches", "worker_isolation", "main2", "user_action_required", "evidence"):
        require(text_field(control.get(key)), "control-plane field absent/invalid")
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


def verify_source(root: Path, fields: dict, head: str) -> None:
    require(bool(SHA.fullmatch(head)), "invalid source HEAD")
    for commit, tree in ((fields["MAIN2_CANONICAL_SHA"], fields["MAIN2_CANONICAL_TREE"]),
                         (fields["KNOWLEDGE_COMMIT"], fields["KNOWLEDGE_TREE"])):
        require(git(root, "rev-parse", commit + "^{tree}") == tree, "source tree not reproducible")
        git(root, "merge-base", "--is-ancestor", commit, head)


def build(root: Path, delta: str = "NONE") -> bytes:
    fields, board, tasks = load_state(root)
    require(text_field(delta) and secret_count(delta) == 0, "invalid or sensitive session delta")
    head = git(root, "rev-parse", "HEAD")
    verify_source(root, fields, head)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", detached=True)
    rows = [f"PROJECT = {fields['PROJECT']}", f"GENERATION = {fields['GENERATION']}", f"CANONICAL_BRANCH = {fields['BASELINE_BRANCH']}", f"CANONICAL_SHA = {fields['MAIN2_CANONICAL_SHA']}", f"CANONICAL_TREE = {fields['MAIN2_CANONICAL_TREE']}", f"CURRENT_GIT_BRANCH = {branch}", f"CURRENT_GIT_HEAD = {head}", f"PRODUCT_ACCEPTANCE = {fields['PRODUCT_ACCEPTANCE']}", "BOARD = DELIVERY_SNAPSHOT_NOT_LIVE"]
    active = [f"{key}:{w['status']}:{w['task_id']}" for key, w in board["workers"].items() if w["status"] in {"RUNNING", "READY", "BLOCKED"}]
    rows += ["ACTIVE_WORKERS = " + ("; ".join(active) or "NONE"), "UNKNOWN_WORKERS = " + (",".join(k for k, w in board["workers"].items() if w["status"] == "UNKNOWN") or "NONE")]
    for state in ("READY", "BLOCKED", "NOT_DISPATCHED", "UNKNOWN"):
        rows.append(state + "_TASKS = " + (",".join(k for k, t in tasks.items() if t["state"] == state) or "NONE"))
    actions = [f"- {key}: {task['user_action_required']}" for key, task in tasks.items() if task["state"] != "DONE" and task["user_action_required"]]
    rows += ["USER_ACTION_REQUIRED =" if actions else "USER_ACTION_REQUIRED = NONE"] + actions
    rows += ["READ_FIRST = " + json.dumps(READ_FIRST), f"SESSION_DELTA = {delta}", "DETAILS = docs/governance/DEPENDENCY_QUEUE.json; unknown does not authorize dispatch."]
    output = ("\n".join(rows) + "\n").encode("utf-8")
    require(len(output) <= 4096, "handoff exceeds 4096 bytes; shorten source snapshot/delta explicitly")
    require(secret_count(output.decode("utf-8")) == 0, "obvious secret material in output")
    return output


def build_web(root: Path) -> bytes:
    fields, board, tasks = load_state(root)
    # A tracked document cannot embed its own commit ID. Ignore only the derived
    # file when finding the latest source commit; every other change invalidates it.
    snapshot = git(root, "log", "-1", "--format=%H", "HEAD", "--", *SOURCE_PATHS)
    verify_source(root, fields, snapshot)
    git(root, "merge-base", "--is-ancestor", snapshot, "HEAD")
    require(not git(root, "diff", "--name-only", snapshot, "HEAD", "--", *SOURCE_PATHS),
            "HEAD contains newer source than web snapshot")
    require(not git(root, "diff", "--name-only", "HEAD", "--", *SOURCE_PATHS),
            "commit source changes before generating web handoff")
    require(not git(root, "diff", "--cached", "--name-only", "HEAD", "--", *SOURCE_PATHS),
            "commit staged source changes before generating web handoff")
    require(not git(root, "ls-files", "--others", "--exclude-standard", "--", *SOURCE_PATHS),
            "untracked source files before web generation")
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", detached=True)
    require(branch in {fields["INTEGRATION_BRANCH"], "DETACHED"}, "wrong integration checkout")
    for path in WEB_INPUTS:
        read(root, path)
        git(root, "cat-file", "-e", f"{snapshot}:{path}")
    generated_at = datetime.fromtimestamp(int(git(root, "show", "-s", "--format=%ct", snapshot)),
                                          timezone.utc).isoformat().replace("+00:00", "Z")
    control = board["control_plane"]
    knowledge = board["knowledge_delivery"]
    rows = ["# MentorFlow web session handoff", "", "**DERIVED / NOT SOURCE OF TRUTH**", "", "```text",
            f"REPOSITORY = {board['repository_url']}", f"PROJECT = {fields['PROJECT']}",
            f"GENERATION = {fields['GENERATION'].lower()}", f"KNOWLEDGE_REF = {knowledge['ref']}",
            f"KNOWLEDGE_COMMIT = {knowledge['commit']}", f"KNOWLEDGE_TREE = {knowledge['tree']}",
            f"CURRENT_INTEGRATION_REF = {fields['INTEGRATION_BRANCH']}",
            f"CURRENT_INTEGRATION_COMMIT = {snapshot}",
            "COMMIT_KIND = SOURCE_SNAPSHOT; publication HEAD is supplied by ref/immutable receipt.",
            "ACTIVE_SLOTS = " + "/".join(control["active_slots"]),
            "SLOT_STATE = " + "; ".join(f"{k}:{w['status']}" for k, w in board["workers"].items()),
            f"A_AT_DISPATCH = {control['a_status_at_dispatch']}",
            f"SETUP = {control['repository_setup']}; {control['branch_picker']}",
            f"DISPATCHES = {control['worker_dispatches']}",
            f"ISOLATION = A:{control['a_isolation']}; {control['worker_isolation']}",
            f"MAIN2 = {control['main2']}; {fields['MAIN2_CANONICAL_SHA']}",
            f"PRODUCT_ACCEPTANCE = {fields['PRODUCT_ACCEPTANCE']}"]
    blockers = [key for key, task in tasks.items() if task["state"] in {"BLOCKED", "UNKNOWN"}]
    actions = [f"{key}: {task['user_action_required']}" for key, task in tasks.items()
               if task["state"] != "DONE" and task["user_action_required"]]
    if control["user_action_required"] != "NONE":
        actions.insert(0, control["user_action_required"])
    rows += ["BLOCKERS = " + (", ".join(blockers) or "NONE"),
             "USER_ACTION_REQUIRED = " + ("; ".join(actions) or "NONE"),
             "```",
             "Details/owner authority remain in the queue; unknown does not authorize dispatch.",
             "", "READ_FIRST:"]
    for path in ("docs/handoff/README.md", *READ_FIRST, "docs/PRODUCT_FLOW.md", "docs/project/COMPONENT_MAP.md"):
        target = "../" + path[len("docs/"):] if path.startswith("docs/") else "../../" + path
        rows.append(f"- [{Path(path).name}]({target})")
    rows += ["", "```text", f"GENERATED_FROM = {snapshot}; " + ", ".join(WEB_INPUTS),
             f"generated_at = {generated_at}", "```",
             "Timestamp is source commit time; board is a receipt-derived snapshot, not live telemetry."]
    output = ("\n".join(rows) + "\n").encode("utf-8")
    require(len(output) <= 4096, "web handoff exceeds 4096 bytes; shorten authoritative snapshot explicitly")
    require(secret_count(output.decode("utf-8")) == 0, "obvious secret material in web output")
    return output


def check_output(root: Path, path: str, expected: bytes) -> None:
    destination = safe_path(root, path)
    require(destination.is_file() and destination.read_bytes() == expected, "handoff absent/stale; regenerate")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-delta", default="NONE")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--web", action="store_true", help="generate/check committed web entry from exact committed source")
    args = parser.parse_args()
    try:
        require(not args.web or args.session_delta == "NONE", "web handoff derives only from committed authority")
        output = build_web(ROOT) if args.web else build(ROOT, args.session_delta)
        path = WEB_OUTPUT if args.web else OUTPUT
        destination = safe_path(ROOT, path)
        if args.check:
            check_output(ROOT, path, output)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(output)
        print(f"HANDOFF_BYTES={len(output)} PATH={path} STATUS=PASS")
    except (ValueError, OSError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f"HANDOFF=FAIL ({type(exc).__name__}); validate local state and required fields.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
