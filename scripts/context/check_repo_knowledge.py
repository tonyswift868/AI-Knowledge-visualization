"""Lightweight maps, schema, local Markdown links and obvious-secret checks."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

from build_compact_handoff import ROOT, OUTPUT, WEB_OUTPUT, build, build_web, check_output, read, require, safe_path, secret_count, no_duplicate_keys, load_state, verify_source, git
import json

LIMITS = {"AGENTS.md": 120, "ARCHITECTURE.md": 300, "docs/project/CURRENT_STATE.md": 180, "docs/exec-plans/active/MAIN3.md": 200}
KNOWLEDGE_DIRS = ("docs/project", "docs/governance", "docs/decisions", "docs/exec-plans", "docs/handoff", "scripts/context")
EXTRA_KNOWLEDGE = ("docs/PRODUCT_FLOW.md", "docs/E109_AUTH_ISSUER_MIGRATION.md", "docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md",
                   "docs/windows-update-release-contract-v1.md", "installer/inno/README.md",
                   "docs/enterprise/staging-orchestrator.md")


def without_code(text: str) -> str:
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            lines.append("")
        else:
            lines.append("" if fence else re.sub(r"`[^`\n]*`", "", line))
    return "\n".join(lines)


def link_failures(root: Path, documents):
    failures, count = [], 0
    for file in documents:
        relative = file.relative_to(root).as_posix()
        text = without_code(read(root, relative))
        targets = re.findall(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", text)
        targets += re.findall(r"(?m)^\s*\[[^\]\n]+\]:\s*(<[^>]+>|\S+)", text)
        targets += re.findall(r'<(?:a|img)\b[^>]*(?:href|src)=["\']([^"\']+)', text, re.I)
        for target in targets:
            target = target.strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            count += 1
            local = unquote(parsed.path)
            path = (root / local.lstrip("/")) if local.startswith("/") else file.parent / local
            try:
                require(path.resolve().is_relative_to(root.resolve()), "link escapes repository")
                rel = path.relative_to(root).as_posix()
                require(safe_path(root, rel).exists(), "link target absent")
            except ValueError:
                failures.append(f"{relative}: {target}")
    return failures, count


EXPECTED_ERRORS = (ValueError, OSError, TypeError, KeyError)


def diagnostic(exc, fallback):
    """Return only static reason IDs; exception text may contain sensitive values."""
    if isinstance(exc, json.JSONDecodeError):
        return "JSON_PARSE_FAILED"
    message = str(exc)
    for fragment, reason in (
        ("duplicate JSON key", "DUPLICATE_JSON_KEY"),
        ("missing/oversized input", "KNOWLEDGE_INPUT_MISSING_OR_OVERSIZED"),
        ("redirected repository", "UNSAFE_INPUT_PATH"),
        ("path escapes repository", "UNSAFE_INPUT_PATH"),
        ("absolute input path", "UNSAFE_INPUT_PATH"),
        ("commit source changes", "DIRTY_SOURCE"),
        ("commit staged source", "DIRTY_SOURCE"),
        ("untracked source", "UNTRACKED_SOURCE"),
        ("source tree not reproducible", "SOURCE_TREE_NOT_REPRODUCIBLE"),
        ("4096 bytes", "HANDOFF_SIZE_LIMIT"),
        ("obvious secret", "SECRET_PATTERN_FOUND"),
        ("dependency", "QUEUE_DEPENDENCY_INVALID"),
        ("unlock edge", "QUEUE_DEPENDENCY_INVALID"),
    ):
        if fragment in message:
            return reason
    return fallback


def safe_label(path):
    # Emit repository-relative file identifiers only, never exception text or link targets.
    return path if re.fullmatch(r"[A-Za-z0-9_./-]+", path) and not secret_count(path) else "REDACTED_PATH"


def scan(root, session_delta="NONE"):
    """Run independent gates even after failure; continue per file within scan gates."""
    stages = {name: [] for name in (
        "LINE_BUDGET", "STATE_BOARD_QUEUE", "COMPACT_HANDOFF", "WEB_HANDOFF",
        "INTERNAL_LINKS", "JSON_PARSE_DUPLICATE_KEYS", "SECRET_PATTERN")}

    def attempt(stage, action, reason, path=""):
        try:
            action()
        except EXPECTED_ERRORS as exc:
            stages[stage].append((diagnostic(exc, reason), safe_label(path)))

    for name, limit in LIMITS.items():
        attempt("LINE_BUDGET", lambda n=name, l=limit: require(len(read(root, n).splitlines()) <= l,
                "line budget exceeded"), "LINE_BUDGET_EXCEEDED", name)

    def state():
        fields, board, _tasks = load_state(root)
        verify_source(root, fields, git(root, "rev-parse", "HEAD"))
        if "current_stage" in board:
            queue = json.loads(read(root, "docs/governance/DEPENDENCY_QUEUE.json"), object_pairs_hook=no_duplicate_keys)
            require(board["authority"] == queue["authority"], "state disagreement: authority")
            require(board["current_stage"] == queue["current_stage"], "state disagreement: current stage")
            for key, value in board["current_stage"].items():
                require(fields.get(key) == value, "state disagreement: current field")
    attempt("STATE_BOARD_QUEUE", state, "STATE_DISAGREEMENT")
    attempt("COMPACT_HANDOFF", lambda: check_output(root, OUTPUT, build(root, session_delta)), "STALE_COMPACT_HANDOFF")
    attempt("WEB_HANDOFF", lambda: check_output(root, WEB_OUTPUT, build_web(root)), "STALE_WEB_HANDOFF")

    docs = sorted((root / "docs").rglob("*.md")) + [root / n for n in ("AGENTS.md", "ARCHITECTURE.md", "README.md", "installer/inno/README.md")]
    for file in docs:
        attempt("INTERNAL_LINKS", lambda f=file: require(not link_failures(root, [f])[0],
                "broken internal documentation links"), "BROKEN_INTERNAL_LINK", file.relative_to(root).as_posix())

    files = [root / n for n in ("AGENTS.md", "ARCHITECTURE.md", "README.md", ".gitignore", ".gitattributes", "docs/legacy-koikatsu/KKS_SPARK_TASK_COMPLETION_REPORT.md")]
    for directory in KNOWLEDGE_DIRS:
        files.extend(p for p in (root / directory).rglob("*") if p.suffix in {".md", ".json", ".py"})
    files.extend(root / name for name in EXTRA_KNOWLEDGE)
    for file in sorted(set(files)):
        relative = file.relative_to(root).as_posix()
        if file.suffix == ".json":
            attempt("JSON_PARSE_DUPLICATE_KEYS", lambda n=relative: json.loads(read(root, n), object_pairs_hook=no_duplicate_keys),
                    "JSON_PARSE_FAILED", relative)
        attempt("SECRET_PATTERN", lambda n=relative: require(secret_count(read(root, n)) == 0,
                "obvious secret material found"), "SECRET_PATTERN_FOUND", relative)
    return stages


def report(stages):
    failures = 0
    for stage, findings in stages.items():
        print(f"{stage} = {'FAIL' if findings else 'PASS'}")
        for reason, path in findings:
            print(f"STAGE={stage} RESULT=FAIL REASON_ID={reason} INPUT={path or 'STAGE_CONTRACT'}")
        failures += len(findings)
    print(f"KNOWLEDGE_FAILURE_COUNT={failures}")
    print(f"KNOWLEDGE_LINT={'FAIL' if failures else 'PASS'}")
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-delta", default="NONE")
    args = parser.parse_args()
    return report(scan(ROOT, args.session_delta))


if __name__ == "__main__":
    sys.exit(main())
