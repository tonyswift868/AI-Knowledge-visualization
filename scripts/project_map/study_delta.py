#!/usr/bin/env python3
"""Read-only Git change map for previously studied modules.

Usage: python study_delta.py --from <learned-commit> [--to <commit>] [--learned M01 M02]
By default the repository is the project root inferred from this file, the
module map is adjacent study_modules.json, and JSON/Markdown output is written
to <repo>/.local/project-map/. --out selects another output directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Any


SCHEMA_VERSION = 1
ISSUE_RE = re.compile(r"\b(?:BUG|DOC)-\d+\b")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
DECL_RE = re.compile(r"(?:async\s+def|def|class|function|func|fn|interface|type|struct|enum)\s+([A-Za-z_$][\w$]*)")
JS_BINDING_RE = re.compile(r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODULES = Path(__file__).with_name("study_modules.json")
DEFAULT_OUT = PROJECT_ROOT / ".local" / "project-map"
MAX_MODULE_FILE_BYTES = 1024 * 1024
SENSITIVE_PATH_PARTS = {"auth.json", "release-keys", "server", "credentials", "credential",
                        "interview", "exam285"}


class DeltaError(Exception):
    pass


def _git(repo: Path, *args: str, check: bool = True, text: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(repo), *args]
    env = os.environ.copy()
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, env=env)
    if check and result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DeltaError(f"git {args[0] if args else ''} failed ({result.returncode}): {detail}")
    if text:
        result.stdout = result.stdout.decode("utf-8", "replace")
        result.stderr = result.stderr.decode("utf-8", "replace")
    return result


def _validate_ref(value: str) -> None:
    if value != "ROOT" and (not REF_RE.fullmatch(value) or value.startswith("-")):
        raise DeltaError(f"invalid revision syntax: {value!r}")


def _resolve(repo: Path, ref: str) -> str:
    _validate_ref(ref)
    if ref == "ROOT":
        return EMPTY_TREE
    result = _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if result.returncode:
        raise DeltaError(f"revision not found or not a commit: {ref}")
    return result.stdout.decode("ascii", "replace").strip()


def _safe_path(value: str) -> str:
    p = PurePosixPath(value.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or not p.parts:
        raise DeltaError(f"module path must stay inside the repository: {value!r}")
    return p.as_posix()


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _reject_sensitive_path(path: Path) -> None:
    parts = [part.casefold() for part in path.parts]
    if any(part in SENSITIVE_PATH_PARTS for part in parts):
        raise DeltaError("path points into a protected or sensitive location")
    if path.name.casefold() == "auth.json" or any(word in path.name.casefold() for word in ("token", "key", "secret", "password")):
        raise DeltaError("path points to a protected or sensitive filename")
    absolute = path.absolute()
    for parent in (Path("D:/MentorFlowApp"), Path("D:/MentorFlowData")):
        if _within(absolute, parent):
            raise DeltaError("user data paths are not valid module-map inputs or outputs")
    if absolute.drive.upper() == "E:":
        raise DeltaError("E: is read-only for this task")


def _validated_modules_path(path: Path) -> Path:
    absolute = path.absolute()
    _reject_sensitive_path(absolute)
    # Check each existing lexical component before resolve so junctions are not followed.
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if current.exists() and _is_reparse(current):
            raise DeltaError("module map cannot be read through a link or reparse point")
    if not path.is_file():
        raise DeltaError("module map must be a regular JSON file or inline JSON")
    if path.stat().st_size > MAX_MODULE_FILE_BYTES:
        raise DeltaError("module map exceeds the 1 MiB metadata limit")
    return path


def _validated_out(path: Path, repo: Path) -> Path:
    absolute = path.absolute()
    _reject_sensitive_path(absolute)
    resolved = absolute.resolve(strict=False)
    repo_local = repo.resolve(strict=True) / ".local" / "project-map"
    office_root = Path("D:/MentorFlow-office")
    if _within(resolved, office_root) and not _within(resolved, repo_local):
        raise DeltaError("output cannot write another office station or repository files")
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current = current / component
        if current.exists() and _is_reparse(current):
            raise DeltaError("output cannot traverse a link or reparse point")
    for name in ("study-delta.json", "study-delta.md"):
        target = absolute / name
        if target.exists() and (_is_reparse(target) or target.stat().st_nlink > 1):
            raise DeltaError("output files cannot be links or hard links")
    return resolved


def _load_modules(value: str | Path | dict[str, Any], learned_override: list[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(value, dict):
        data = value
    else:
        raw = str(value)
        if len(raw.encode("utf-8")) > MAX_MODULE_FILE_BYTES:
            raise DeltaError("module map exceeds the 1 MiB metadata limit")
        try:
            if raw.lstrip().startswith(("{", "[")):
                data = json.loads(raw)
            else:
                candidate = _validated_modules_path(Path(raw))
                with candidate.open("r", encoding="utf-8") as stream:
                    data = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise DeltaError(f"could not read module JSON: {exc}") from exc
    modules = data.get("modules")
    if not isinstance(modules, list) or not modules:
        raise DeltaError("module JSON must contain a non-empty 'modules' array")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in modules:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise DeltaError("each module needs a string id and paths array")
        module_id = item["id"]
        paths = item.get("paths")
        if module_id in seen or not isinstance(paths, list) or not paths:
            raise DeltaError(f"duplicate module id or missing paths: {module_id}")
        seen.add(module_id)
        normalized.append({
            "id": module_id,
            "title": str(item.get("title", module_id)),
            "paths": [_safe_path(str(path)) for path in paths],
            "background_paths": [_safe_path(str(path)) for path in item.get("background_paths", [])],
            "background_scope": str(item.get("background_scope", "")),
            "learned_at": item.get("learned_at"),
        })
    selected = learned_override if learned_override is not None else data.get("learned_modules")
    if selected is None:
        selected_ids = [m["id"] for m in normalized]
    elif isinstance(selected, list) and all(isinstance(i, str) for i in selected):
        selected_ids = selected
        unknown = sorted(set(selected_ids) - seen)
        if unknown:
            raise DeltaError(f"unknown learned module ids: {', '.join(unknown)}")
    else:
        raise DeltaError("learned_modules must be an array of module ids")
    return normalized, selected_ids


def _parse_name_status(raw: bytes) -> list[dict[str, Any]]:
    tokens = raw.split(b"\0")
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens) and tokens[i]:
        status = tokens[i].decode("ascii", "replace")
        i += 1
        if status.startswith("R") or status.startswith("C"):
            if i + 1 >= len(tokens):
                raise DeltaError("truncated rename/copy record from git")
            old_path, new_path = tokens[i].decode("utf-8", "replace"), tokens[i + 1].decode("utf-8", "replace")
            i += 2
            result.append({"status": "R" if status.startswith("R") else "C", "score": status[1:], "old_path": old_path, "new_path": new_path})
        else:
            if i >= len(tokens):
                raise DeltaError("truncated name-status record from git")
            path = tokens[i].decode("utf-8", "replace")
            i += 1
            result.append({"status": status, "old_path": path if status == "D" else None, "new_path": path if status != "D" else None})
    return result


def _parse_numstat(raw: bytes) -> tuple[int, int, int, int]:
    tokens = raw.split(b"\0")
    i = 0
    added = deleted = binary = files = 0
    while i < len(tokens) and tokens[i]:
        fields = tokens[i].split(b"\t", 2)
        i += 1
        if len(fields) != 3:
            continue
        a, d, path = fields
        if not path:  # With -z, a rename has an empty path followed by old and new names.
            i += 2
        files += 1
        if a == b"-" or d == b"-":
            binary += 1
        else:
            added += int(a)
            deleted += int(d)
    return added, deleted, binary, files


def _parse_numstat_records(raw: bytes) -> list[dict[str, Any]]:
    """Retain one insertion/deletion record per changed file, including renames."""
    tokens = raw.split(b"\0")
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens) and tokens[i]:
        fields = tokens[i].split(b"\t", 2)
        i += 1
        if len(fields) != 3:
            raise DeltaError("invalid git numstat record")
        added_raw, deleted_raw, path = fields
        if path:
            old_path = new_path = path.decode("utf-8", "replace")
        else:
            if i + 1 >= len(tokens):
                raise DeltaError("truncated rename numstat record from git")
            old_path = tokens[i].decode("utf-8", "replace")
            new_path = tokens[i + 1].decode("utf-8", "replace")
            i += 2
        binary = added_raw == b"-" or deleted_raw == b"-"
        result.append({
            "old_path": old_path,
            "new_path": new_path,
            "insertions": None if binary else int(added_raw),
            "deletions": None if binary else int(deleted_raw),
            "binary": binary,
        })
    return result


def _change_commits(repo: Path, base: str, head: str, paths: list[str], limit: int = 10) -> dict[str, Any]:
    paths = list(dict.fromkeys(path for path in paths if path))
    if not paths:
        return {"count": 0, "shown": [], "truncated": False}
    if base == EMPTY_TREE:
        result = _git(repo, "log", "--root", "--no-renames", "--format=%H%x09%s", head, "--", *paths, text=True)
    else:
        result = _git(repo, "log", "--no-renames", "--format=%H%x09%s", f"{base}..{head}", "--", *paths, text=True)
    lines = [line for line in result.stdout.splitlines() if line]
    shown = []
    for line in lines[:limit]:
        sha, _, subject = line.partition("\t")
        shown.append({"commit": sha, "subject": subject,
                      "issue_ids": sorted(set(ISSUE_RE.findall(subject)))})
    return {"count": len(lines), "shown": shown, "truncated": len(lines) > limit}


def _annotate_global_changes(repo: Path, base: str, head: str,
                             changes: list[dict[str, Any]],
                             modules: list[dict[str, Any]], selected_ids: list[str]) -> dict[str, Any]:
    """Make every file in the default comparison visible as mapped or unassigned."""
    raw = _git(repo, "diff", "--numstat", "-z", "--find-renames", "--no-ext-diff", "--no-textconv", base, head).stdout
    numstats = _parse_numstat_records(raw)
    if len(numstats) != len(changes):
        raise DeltaError("Git name-status and numstat file counts disagree; refusing to omit changes")
    by_id = {module["id"]: module for module in modules}
    selected_set = set(selected_ids)
    selected_mapped: list[dict[str, Any]] = []
    known_unlearned: list[dict[str, Any]] = []
    unassigned: list[dict[str, Any]] = []
    for change, counts in zip(changes, numstats):
        old_path = change.get("old_path") or ""
        new_path = change.get("new_path") or ""
        touched = [old_path, new_path]
        selected = sorted({m["id"] for m in modules if m["id"] in selected_set
                           and any(_matches(path, m["paths"] + m["background_paths"]) for path in touched)})
        known = sorted({m["id"] for m in modules
                        if any(_matches(path, m["paths"] + m["background_paths"]) for path in touched)})
        item: dict[str, Any] = {
            "status": change["status"], "old_path": old_path, "new_path": new_path,
            "insertions": counts["insertions"], "deletions": counts["deletions"],
            "binary": counts["binary"],
        }
        if selected:
            item["modules"] = selected
            item["related_commits"] = _change_commits(repo, base, head, touched)
            selected_mapped.append(item)
        elif known:
            item["modules"] = known
            item["reason"] = "路径属于模块地图，但这些模块未在本次选择中；仍明确列出，不混入已学回顾。"
            item["related_commits"] = _change_commits(repo, base, head, touched)
            known_unlearned.append(item)
        else:
            item["reason"] = "路径未命中模块对应表；保留为待归属变化。"
            item["related_commits"] = _change_commits(repo, base, head, touched)
            unassigned.append(item)
    return {
        "from_commit": base,
        "to_commit": head,
        "changed_file_records": len(changes),
        "selected_module_records": len(selected_mapped),
        "known_unlearned_records": len(known_unlearned),
        "unassigned_records": len(unassigned),
        "accounting_reconciles": len(selected_mapped) + len(known_unlearned) + len(unassigned) == len(changes),
        "selected_module_paths": selected_mapped,
        "known_but_unlearned_paths": known_unlearned,
        "unassigned_changes": unassigned,
        "line_count_note": "文本文件显示插入/删除行数；二进制文件行数为 null 并标记 binary。提交说明仅作线索。",
    }


def _matches(path: str | None, paths: list[str]) -> bool:
    if path is None:
        return False
    normalized = path.replace("\\", "/")
    return any(normalized == p or normalized.startswith(p.rstrip("/") + "/") for p in paths)


def _tracked_paths(repo: Path, commit: str, module_paths: list[str]) -> set[str]:
    result = _git(repo, "ls-tree", "-r", "--name-only", "-z", commit, "--", *module_paths)
    names = (n.decode("utf-8", "replace") for n in result.stdout.split(b"\0") if n)
    return {name for name in names if _matches(name, module_paths)}


def _module_numstat(repo: Path, base: str, head: str, paths: list[str]) -> tuple[int, int, int, int]:
    result = _git(repo, "diff", "--numstat", "-z", "--find-renames", "--no-ext-diff", "--no-textconv", base, head, "--", *paths)
    return _parse_numstat(result.stdout)


def _function_contexts(repo: Path, base: str, head: str, paths: list[str], limit: int = 40) -> tuple[list[dict[str, str]], bool]:
    cmd = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(repo), "diff", "--function-context", "--unified=0", "--find-renames", "--no-ext-diff", "--no-textconv", base, head, "--", *paths]
    env = os.environ.copy()
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        decoded = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        source = "hunk_context" if decoded.startswith("@@") else "definition_line"
        if source == "hunk_context":
            label = decoded.split("@@", 2)[-1].strip()
            match = DECL_RE.search(label)
            candidate = match.group(1) if match else ""
        elif decoded.startswith(("+", "-", " ")) and not decoded.startswith(("+++", "---")):
            label = decoded[1:].strip()
            match = DECL_RE.search(label) or JS_BINDING_RE.search(label)
            candidate = match.group(1) if match else ""
        else:
            continue
        if candidate and candidate not in seen:
            seen.add(candidate)
            if len(found) < limit:
                found.append({"name": candidate, "source": source, "meaning": "定义位置候选，需按对应源码核对是否是本次变化的函数。"})
    stderr = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    proc.stdout.close()
    if proc.stderr:
        proc.stderr.close()
    code = proc.wait()
    if code:
        raise DeltaError(f"git diff function context failed ({code}): {stderr.strip()}")
    return found, len(seen) > limit


def _module_commits(repo: Path, base: str, head: str, paths: list[str], limit: int = 30) -> tuple[list[dict[str, str]], int]:
    if base == EMPTY_TREE:
        result = _git(repo, "log", "--root", "--no-renames", "--format=%H%x09%s", head, "--", *paths, text=True)
    else:
        result = _git(repo, "log", "--no-renames", "--format=%H%x09%s", f"{base}..{head}", "--", *paths, text=True)
    lines = [line for line in result.stdout.splitlines() if line]
    commits: list[dict[str, str]] = []
    for line in lines[:limit]:
        sha, _, subject = line.partition("\t")
        commits.append({
            "commit": sha,
            "subject": subject,
            "issue_ids": sorted(set(ISSUE_RE.findall(subject))),
            "interpretation": "提交说明仅作线索；变化原因需结合代码与状态记录核对。",
        })
    return commits, len(lines)


def analyze(repo: str | Path, from_ref: str, to_ref: str, module_data: str | Path | dict[str, Any], *, major_change_ratio: float = 0.5, learned_override: list[str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    repo_path = Path(repo).resolve(strict=True)
    if not (repo_path / ".git").exists() and not _git(repo_path, "rev-parse", "--git-dir", check=False).returncode == 0:
        raise DeltaError(f"not a Git working tree: {repo_path}")
    if not 0.0 < major_change_ratio <= 1.0:
        raise DeltaError("major_change_ratio must be greater than 0 and at most 1")
    all_modules, selected_ids = _load_modules(module_data, learned_override)
    selected_set = set(selected_ids)
    modules = [m for m in all_modules if m["id"] in selected_set]
    base, head = _resolve(repo_path, from_ref), _resolve(repo_path, to_ref)
    if base == EMPTY_TREE:
        comparable = True
    else:
        ancestor_check = _git(repo_path, "merge-base", "--is-ancestor", base, head, check=False)
        comparable = ancestor_check.returncode == 0
        if ancestor_check.returncode not in (0, 1):
            raise DeltaError("could not compare commit ancestry")
    changes = _parse_name_status(_git(repo_path, "diff", "--name-status", "-z", "--find-renames", "--no-ext-diff", "--no-textconv", base, head).stdout)
    coverage = _annotate_global_changes(repo_path, base, head, changes, all_modules, selected_ids)
    unassigned = coverage["unassigned_changes"]
    known_unlearned = coverage["known_but_unlearned_paths"]
    changes_by_base = {base: changes}
    modules_out: list[dict[str, Any]] = []
    background_record_keys: set[tuple[str, str]] = set()
    selected_unique_paths: set[str] = set()
    for module in modules:
        module_from_input = module.get("learned_at") or from_ref
        try:
            module_base = _resolve(repo_path, module_from_input)
        except DeltaError as exc:
            raise DeltaError(f"{module['id']} learned_at baseline invalid: {exc}") from exc
        if module_base == EMPTY_TREE:
            module_comparable = True
        else:
            module_ancestor = _git(repo_path, "merge-base", "--is-ancestor", module_base, head, check=False)
            if module_ancestor.returncode not in (0, 1):
                raise DeltaError(f"{module['id']} ancestry comparison failed")
            module_comparable = module_ancestor.returncode == 0
        module_paths_all = module["paths"] + module["background_paths"]
        # Detect renames before path filtering: otherwise a move out of a module
        # looks like a deletion and loses the new location.
        if module_base not in changes_by_base:
            changes_by_base[module_base] = _parse_name_status(_git(repo_path, "diff", "--name-status", "-z", "--find-renames", "--no-ext-diff", "--no-textconv", module_base, head).stdout)
        module_changes = [c for c in changes_by_base[module_base]
                          if _matches(c["old_path"], module_paths_all) or _matches(c["new_path"], module_paths_all)]
        background_changes = [c for c in module_changes if _matches(c["old_path"], module["background_paths"]) or _matches(c["new_path"], module["background_paths"])]
        owned_changes = [c for c in module_changes if c not in background_changes]
        for c in background_changes:
            background_record_keys.add((c["old_path"] or "", c["new_path"] or ""))
        for c in owned_changes:
            selected_unique_paths.update(p for p in (c["old_path"], c["new_path"]) if p)
        paths = list(module["paths"])
        for change in owned_changes:
            if change["status"] == "R":
                paths.extend(p for p in (change["old_path"], change["new_path"]) if p and p not in paths)
        baseline_files = _tracked_paths(repo_path, module_base, paths) if module_base != EMPTY_TREE else set()
        touched_base = {c["old_path"] or c["new_path"] for c in owned_changes}
        touched_count = len(touched_base & baseline_files)
        ratio = touched_count / len(baseline_files) if baseline_files else (1.0 if owned_changes else 0.0)
        num_added, num_deleted, binary_count, num_files = _module_numstat(repo_path, module_base, head, paths) if paths else (0, 0, 0, 0)
        function_names, more_functions = _function_contexts(repo_path, module_base, head, paths)
        commits, commit_count = _module_commits(repo_path, module_base, head, paths)
        def render_changes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            rendered = []
            for c in items:
                item = {"status": c["status"]}
                if c["old_path"]:
                    item["old_path"] = c["old_path"]
                if c["new_path"]:
                    item["new_path"] = c["new_path"]
                if c.get("score"):
                    item["rename_score"] = c["score"]
                rendered.append(item)
            return rendered
        bg_added, bg_deleted, bg_binary, bg_files = _module_numstat(repo_path, module_base, head, module["background_paths"]) if module["background_paths"] else (0, 0, 0, 0)
        bg_commits, bg_commit_count = _module_commits(repo_path, module_base, head, module["background_paths"]) if module["background_paths"] else ([], 0)
        file_items = render_changes(owned_changes)
        background_items = render_changes(background_changes)
        modules_out.append({
            "id": module["id"],
            "title": module["title"],
            "learned_at": module.get("learned_at"),
            "comparison": {"from_input": module_from_input, "from_commit": module_base, "to_commit": head, "is_ancestor": module_comparable},
            "changes": file_items,
            "background_document_changes": {"changes": background_items, "stats": {"files_in_diff": bg_files, "insertions": bg_added, "deletions": bg_deleted, "binary_files": bg_binary}, "commit_summaries": {"count": bg_commit_count, "shown": bg_commits, "truncated": bg_commit_count > len(bg_commits)}, "scope_to_check": module["background_scope"], "interpretation": "背景文件变化不直接计入模块代码变化；按指定段落或编号人工核对。"},
            "stats": {"files_in_diff": num_files, "insertions": num_added, "deletions": num_deleted, "binary_files": binary_count},
            "function_contexts": {"names": function_names, "truncated": more_functions, "note": "由 Git 函数上下文启发式提取；不支持或未识别的语言可能为空，不代表没有函数变化。"},
            "commit_summaries": {"count": commit_count, "shown": commits, "truncated": commit_count > len(commits)},
            "change_share_estimate": {"changed_existing_path_count": touched_count, "known_paths_at_base": len(baseline_files), "ratio": round(ratio, 4), "threshold": major_change_ratio, "suggest_relearning_review": ratio >= major_change_ratio and bool(owned_changes), "note": "原型的可配置路径占比提示，仅供复核，不是产品定论，也不自动改学习进度。"},
            "coverage_note": "仅检查本模块列出的 Git 路径；共享文件可能同时归属多个模块。",
        })
    status = _git(repo_path, "status", "--short", "--untracked-files=all", text=True).stdout
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": str(repo_path),
        "from": {"input": from_ref, "commit": base, "root_empty_tree": base == EMPTY_TREE},
        "to": {"input": to_ref, "commit": head},
        "history": {"is_ancestor": comparable, "note": "若不是祖先关系，按 Git 对两个提交的树差异汇总；这不是线性续学时间线。" if not comparable else "基线是目标提交的祖先。"},
        "working_tree": {"dirty": bool(status.strip()), "note": "工作区变更没有混入提交间比较；本原型仅报告工作区是否有改动。"},
        "selected_modules": selected_ids,
        "summary": {"default_from_change_records": len(changes), "selected_modules_with_changes": sum(bool(m["changes"]) for m in modules_out), "selected_unique_changed_file_paths": len(selected_unique_paths), "shared_background_document_records": len(background_record_keys), "known_but_unlearned_paths": known_unlearned, "truly_unmapped_project_paths": unassigned},
        "change_coverage": coverage,
        "modules": modules_out,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "limitations": [
            "Git diff 显示文本变化，不足以单独证明业务原因；变更说明、CURRENT_STATE 和 BUGS 编号需要人复核。",
            "所有提交间变化文件都会进入模块映射、未选模块或未归属列表；未归属项包含行数和相关提交说明。",
            "只统计两个提交树之间的已提交变化；未提交改动不纳入摘要。",
            "大幅变化占比只用于提示 dots 复核是否建议重学，不自动清除或推进学习状态。",
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Study delta", "",
        f"- 仓库：`{result['repository']}`",
        f"- 比较：`{result['from']['commit']}` → `{result['to']['commit']}`",
        f"- 生成时间：{result['generated_at']}",
        f"- 主仓提交：`{result['main_head']}`", "",
        "## 已选模块", "",
        "| 模块 | 基线 | 变化文件 | 增行 | 删行 | 二进制 |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for module in result["modules"]:
        stats = module["stats"]
        lines.append(f"| {module['id']} {module['title']} | `{module['comparison']['from_commit']}` | {stats['files_in_diff']} | {stats['insertions']} | {stats['deletions']} | {stats['binary_files']} |")
    coverage = result["change_coverage"]
    lines += ["", "## 提交间变化覆盖", "",
              f"总变化文件记录 {coverage['changed_file_records']}；已选模块映射 {coverage['selected_module_records']}；未选模块映射 {coverage['known_unlearned_records']}；未归属 {coverage['unassigned_records']}；核对相等：{coverage['accounting_reconciles']}。", "",
              "| 分类 | 状态 | 文件 | 增行 | 删行 | 二进制 | 对应模块/说明 | 相关提交说明（线索） |",
              "|---|---|---:|---:|---:|---:|---|---|"]
    records = [("已选模块", x) for x in coverage["selected_module_paths"]]
    records += [("未选模块", x) for x in coverage["known_but_unlearned_paths"]]
    records += [("未归属", x) for x in coverage["unassigned_changes"]]
    for category, item in records:
        paths = f"{item['old_path']} → {item['new_path']}" if item["old_path"] != item["new_path"] else item["new_path"]
        commits = "; ".join(c["subject"].replace("|", "\\|") for c in item.get("related_commits", {}).get("shown", [])) or "—"
        module_names = ", ".join(item.get("modules", [])) or item.get("reason", "")
        lines.append(f"| {category} | {item['status']} | `{paths}` | {item.get('insertions') if item.get('insertions') is not None else '—'} | {item.get('deletions') if item.get('deletions') is not None else '—'} | {'是' if item.get('binary') else '否'} | {module_names} | {commits} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(PROJECT_ROOT), help="Git 仓库路径；默认从本脚本位置推算")
    parser.add_argument("--from", dest="from_ref", required=True, help="上次学习提交；ROOT 表示空树")
    parser.add_argument("--to", default="HEAD", help="目标提交，默认 HEAD")
    parser.add_argument("--modules", default=str(DEFAULT_MODULES), help="模块 JSON 路径；默认使用脚本旁 study_modules.json")
    parser.add_argument("--learned", nargs="+", help="覆盖 JSON 内的已学模块列表，例如 --learned M01 M02")
    parser.add_argument("--major-change-ratio", type=float, default=0.5, help="建议复核是否重学的映射路径占比阈值")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录；默认 <仓库>/.local/project-map")
    args = parser.parse_args(argv)
    try:
        result = analyze(args.repo, args.from_ref, args.to, args.modules, major_change_ratio=args.major_change_ratio, learned_override=args.learned)
        result["generated_at"] = __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
        result["main_head"] = _git(Path(args.repo), "rev-parse", "refs/heads/main", check=False).stdout.decode("ascii", "replace").strip() or None
    except (DeltaError, OSError) as exc:
        print(f"study_delta: {exc}", file=sys.stderr)
        return 2
    try:
        out = _validated_out(Path(args.out), Path(args.repo))
        out.mkdir(parents=True, exist_ok=True)
    except (DeltaError, OSError) as exc:
        print(f"study_delta: {exc}", file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    (out / "study-delta.json").write_text(rendered, encoding="utf-8")
    (out / "study-delta.md").write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"json": str(out / "study-delta.json"), "markdown": str(out / "study-delta.md"), "elapsed_seconds": result["elapsed_seconds"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
