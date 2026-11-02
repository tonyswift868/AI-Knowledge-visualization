#!/usr/bin/env python3
"""Report the seven MentorFlow office repos against local main without Git writes.

Usage::

    python scripts/project_map/office_status.py
    python scripts/project_map/office_status.py --main-repo D:/MentorFlow \\
        --office-root D:/MentorFlow-office --out D:/MentorFlow/.local/project-map

By default the main repository root is inferred from this script's location,
the office root defaults to ``D:/MentorFlow-office``, and output is written to
``<repo>/.local/project-map``. No fetch, checkout, index refresh, or
configuration write is performed. See ``--help`` for options.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAIN = REPO_ROOT
DEFAULT_OFFICE = Path("D:/MentorFlow-office")
DEFAULT_OUT = REPO_ROOT / ".local" / "project-map"
FORBIDDEN_OUTPUT_ROOTS = (
    Path("D:/MentorFlowApp"), Path("D:/MentorFlowData"),
    Path("D:/MentorFlow/.local/interview"),
    Path("D:/MentorFlow/.local/test-tmp/exam285"),
    Path("D:/MentorFlow/.local/release-keys"), Path("D:/MentorFlow/.local/server"),
)
STATIONS = ("classroom", "client", "library", "platform", "runtime", "server", "tools")


def _run_git(repo: Path, args: list[str], *, check: bool = True) -> bytes:
    """Run a git read command without optional index refresh or fsmonitor writes."""
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    cmd = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(repo), *args]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, check=False)
    if check and result.returncode:
        raise RuntimeError("git command unavailable or repository unreadable")
    return result.stdout


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


def _safe_name(value: str) -> str:
    # Keep the path itself but make control characters visible and single-line.
    return "".join(ch if ch >= " " and ch != "\x7f" else f"\\u{ord(ch):04x}" for ch in value)


def _dirty_paths(raw: bytes) -> list[str]:
    """Parse porcelain-v1 -z records; rename records carry a second NUL path."""
    fields = raw.split(b"\0")
    paths: list[str] = []
    i = 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if not record:
            continue
        # In -z mode rename/copy paths are emitted destination then source.
        name = _safe_name(_decode(record[3:]))
        paths.append(name)
        status = _decode(record[:2])
        if "R" in status or "C" in status:
            if i < len(fields) and fields[i]:
                paths.append(_safe_name(_decode(fields[i])))
                i += 1
    return sorted(set(paths), key=str.casefold)


def _station_status(name: str, repo: Path, main_oid: str) -> dict[str, Any]:
    row: dict[str, Any] = {"station": name, "repo": str(repo)}
    for node in (repo, repo.parent):
        if node.is_symlink() or (node.exists() and getattr(node.lstat(), 'st_file_attributes', 0) & 0x400):
            row.update(available=False, error='已排除链接/重解析点工位')
            return row
    if not repo.is_dir():
        row.update({"available": False, "error": "工位 repo 目录不存在"})
        return row
    try:
        row["branch"] = _decode(_run_git(repo, ["symbolic-ref", "--short", "-q", "HEAD"], check=False)).strip() or "(detached HEAD)"
        row["head"] = _decode(_run_git(repo, ["rev-parse", "HEAD"]).strip())
        row["head_time"] = _decode(_run_git(repo, ["show", "-s", "--format=%cI", "HEAD"]).strip())
        row["recent_subject"] = _decode(_run_git(repo, ["log", "-1", "--format=%s", "HEAD"]).strip()).splitlines()[0:1]
        row["recent_subject"] = row["recent_subject"][0] if row["recent_subject"] else ""
        row["dirty_files"] = _dirty_paths(_run_git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]))
        # cat-file is read-only and confirms the main commit exists locally. Suppress its stderr.
        env = os.environ.copy()
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        verify = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(repo),
             "cat-file", "-e", f"{main_oid}^{{commit}}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, check=False,
        )
        if verify.returncode:
            row["comparison"] = {"available": False, "reason": "主仓 main 提交对象不在该工位本地对象库；未 fetch，故不比较"}
        else:
            counts = _decode(_run_git(repo, ["rev-list", "--left-right", "--count", f"HEAD...{main_oid}"])).split()
            if len(counts) != 2:
                row["comparison"] = {"available": False, "reason": "git 未返回可解析的 ahead/behind 计数"}
            else:
                row["comparison"] = {"available": True, "ahead": int(counts[0]), "behind": int(counts[1]),
                                     "against": "主仓库 main", "main_head": main_oid}
        row["available"] = True
    except (OSError, RuntimeError, IndexError, ValueError):
        # Do not echo stderr: it can contain arbitrary path or repository text.
        row.update({"available": False, "error": "只读 Git 查询失败；未输出命令错误内容"})
    return row


def collect(main_repo: Path, office_root: Path) -> dict[str, Any]:
    generated = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        main_oid = _decode(_run_git(main_repo, ["rev-parse", "refs/heads/main"]).strip())
    except (OSError, RuntimeError):
        main_oid = "unavailable"
    stations = []
    for name in STATIONS:
        repo = office_root / name / "repo"
        if main_oid == "unavailable":
            stations.append({"station": name, "repo": str(repo), "available": False,
                             "error": "无法从主仓库读取 refs/heads/main；不猜测比较目标"})
        else:
            stations.append(_station_status(name, repo, main_oid))
    return {
        "generated_at": generated,
        "main_repo": str(main_repo),
        "main_head": main_oid,
        "note": "七个工位 repo 当前是主仓库的 Git worktree；这里只列与主线的提交差异和工作区文件名，不重复分析源码内容。",
        "read_only": True,
        "stations": stations,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MentorFlow 工位状态",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 主仓库 HEAD（refs/heads/main）：`{report['main_head']}`",
        f"- 说明：{report['note']}",
        "",
        "| 工位 | 分支 | HEAD | 提交时间 | 相对 main（领先/落后） | 工作区改动文件 | 最近提交说明 | 状态 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in report["stations"]:
        comparison = row.get("comparison", {})
        delta = (f"{comparison['ahead']} / {comparison['behind']}" if comparison.get("available")
                 else comparison.get("reason", "不可比较"))
        dirty = ", ".join(f"`{x}`" for x in row.get("dirty_files", [])) or "干净"
        lines.append("| " + " | ".join([
            row["station"], f"`{row.get('branch', '—')}`", f"`{row.get('head', '—')}`",
            row.get("head_time", "—"), delta, dirty,
            row.get("recent_subject", "—").replace("|", "\\|"),
            "可读" if row.get("available") else row.get("error", "不可读"),
        ]) + " |")
    lines.extend(["", "相对 main 的计数定义：领先是工位 HEAD 独有提交数，落后是 main 独有提交数。只读查询不 fetch，也不刷新索引锁。", ""])
    return "\n".join(lines)


def _is_reparse(path: Path) -> bool:
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _ensure_out(out: Path, *, repo_root: Path = REPO_ROOT, office_root: Path = DEFAULT_OFFICE) -> Path:
    out = out.absolute()
    for protected in (*FORBIDDEN_OUTPUT_ROOTS,
                      *(office_root / station / "repo" for station in STATIONS if station != "platform")):
        if _is_within(out, protected):
            raise ValueError("--out 不能指向用户数据、受限目录或其他工位 repo")
    # Reject junctions in every existing output ancestor before resolve() follows one.
    for ancestor in (out, *out.parents):
        if ancestor.exists() and _is_reparse(ancestor):
            raise ValueError("输出路径不能经过链接/重解析点")
        if ancestor == ancestor.parent:
            break
    for name in ('office-status.json', 'office-status.md'):
        target = out / name
        if target.is_symlink() or (target.exists() and _is_reparse(target)):
            raise ValueError('输出文件不得为链接')
        if target.exists() and target.stat(follow_symlinks=False).st_nlink > 1:
            raise ValueError('输出文件不得为硬链接')
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_MAIN,
                        help="主仓库根目录；默认按此脚本位置推算")
    parser.add_argument("--main-repo", type=Path, default=None,
                        help="main 所在仓库；省略时使用 --repo")
    parser.add_argument("--office-root", type=Path, default=DEFAULT_OFFICE,
                        help="七个工位 repo 的父目录；默认按 platform/repo 位置推算")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="输出目录；默认 <repo>/.local/project-map")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        main_repo = args.main_repo or args.repo
        out_dir = _ensure_out(args.out, repo_root=args.repo, office_root=args.office_root)
        report = collect(main_repo, args.office_root)
        report["duration_seconds"] = round(time.perf_counter() - started, 3)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "office-status.json"
        md_path = out_dir / "office-status.md"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(json.dumps({"json": str(json_path), "markdown": str(md_path),
                          "duration_seconds": report["duration_seconds"]}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        # Keep errors generic to prevent accidental repository diagnostics leaking.
        print(f"office status failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
