"""Lightweight maps, schema, local Markdown links and obvious-secret checks."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

from build_compact_handoff import ROOT, OUTPUT, WEB_OUTPUT, build, build_web, check_output, read, require, safe_path, secret_count, no_duplicate_keys
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-delta", default="NONE")
    args = parser.parse_args()
    try:
        for name, limit in LIMITS.items():
            lines = len(read(ROOT, name).splitlines())
            require(lines <= limit, f"line budget exceeded: {name}")
            print(f"{name}: {lines}/{limit} lines")
        output = build(ROOT, args.session_delta)
        check_output(ROOT, OUTPUT, output)
        web = build_web(ROOT)
        check_output(ROOT, WEB_OUTPUT, web)
        docs = sorted((ROOT / "docs").rglob("*.md")) + [ROOT / n for n in ("AGENTS.md", "ARCHITECTURE.md", "README.md", "installer/inno/README.md")]
        failures, links = link_failures(ROOT, docs)
        for failure in failures:
            print("BROKEN_LINK " + failure, file=sys.stderr)
        require(not failures, "broken internal documentation links")
        files = [ROOT / n for n in ("AGENTS.md", "ARCHITECTURE.md", "README.md", ".gitignore", ".gitattributes", "docs/legacy-koikatsu/KKS_SPARK_TASK_COMPLETION_REPORT.md")]
        for directory in KNOWLEDGE_DIRS:
            files.extend(p for p in (ROOT / directory).rglob("*") if p.suffix in {".md", ".json", ".py"})
        files.extend(ROOT / name for name in EXTRA_KNOWLEDGE)
        files = sorted(set(files))
        total = 0
        json_files = 0
        for file in files:
            relative = file.relative_to(ROOT).as_posix()
            contents = read(ROOT, relative)
            if file.suffix == ".json":
                json.loads(contents, object_pairs_hook=no_duplicate_keys)
                json_files += 1
            count = secret_count(contents)
            if count:
                print(f"SECRET_PATTERN path={relative} count={count}", file=sys.stderr)
            total += count
        require(total == 0, "obvious secret material found")
        print(f"KNOWLEDGE_LINT=PASS JSON_PARSE=PASS JSON_FILES={json_files} LINKS={links} HANDOFF_BYTES={len(output)} WEB_HANDOFF_BYTES={len(web)} SECRET_VALUE_COUNT=0 SECRET_FILES={len(files)}")
        print("SECRET_SCOPE=knowledge maps/state/docs/context tools + extended auth/update/installer/staging contracts + generated outputs; obvious patterns only, not repository/history audit")
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(f"KNOWLEDGE_LINT=FAIL ({type(exc).__name__}); check local knowledge inputs/links/handoff.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
