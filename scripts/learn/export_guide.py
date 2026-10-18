"""Build a clean, deterministic Markdown repository from MentorFlow's learning guide."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid


class GuideExportError(Exception):
    """Raised when source material is incomplete or contains sensitive information."""

    def __init__(self, message: str, findings: list[dict] | None = None):
        self.findings = findings or []
        super().__init__(message)


PROVENANCE_HEADING = re.compile(r"^#{1,6}\s+学习路线：本(?:节|题|篇)出处\s*$")
CLAIM_CODE = r"(?:[A-Z]{1,8}(?:-[A-Z]{1,8})?-\d{1,3}|[A-Z]{1,6}\d{1,3})"
CLAIM_MARKER = re.compile(rf"\[{CLAIM_CODE}(?:\s*(?:,|、|—)\s*{CLAIM_CODE})*\]")
SENSITIVE_PATTERNS = (
    ("server_ip", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")),
    ("email", re.compile(r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Z]{2,}(?![\w.-])")),
    ("local_path", re.compile(r"(?i)(?:\b[A-Z]:[\\/][^\s`<>\"']+|\\\\[\w.-]+\\[\w.$ -]+|/(?:home|users|etc|var|opt|srv|mnt|root)/[^\s`<>\"']+)")),
    ("account_id", re.compile(r"(?i)(?:账号|账户|用户)(?:编号|ID|号)\s*[:：#]?\s*[A-Z0-9_-]{4,}|\bmfa1_[A-Za-z0-9_-]{8,}\b")),
    ("credential", re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|gh[pousr]_[A-Za-z0-9]{20,}|mfn1_[A-Za-z0-9_-]{24,})\b")),
    ("secret_assignment", re.compile(r"(?i)\b(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|password|密钥值|密钥内容)\s*[:=：]\s*['\"]?[A-Za-z0-9_./+-]{12,}")),
)


def _strip_provenance(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    dropping = False
    section_level = 0
    for line in lines:
        if PROVENANCE_HEADING.match(line.strip()):
            dropping = True
            section_level = len(line) - len(line.lstrip("#"))
            while kept and not kept[-1].strip():
                kept.pop()
            continue
        if dropping:
            heading = re.match(r"^(#{1,6})\s+", line)
            if heading and len(heading.group(1)) <= section_level:
                dropping = False
                kept.append(line)
            continue
        kept.append(line)
    cleaned = "\n".join(kept)
    cleaned = CLAIM_MARKER.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip() + "\n"


def _provenance_only(text: str) -> list[str]:
    lines = text.splitlines()
    sections: list[str] = []
    collecting = False
    section_level = 0
    current: list[str] = []
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+", line)
        if collecting and heading and len(heading.group(1)) <= section_level:
            sections.extend(current)
            sections.append("")
            current = []
            collecting = False
        if PROVENANCE_HEADING.match(line.strip()):
            collecting = True
            section_level = len(line) - len(line.lstrip("#"))
            current = [line, ""]
        elif collecting:
            current.append(line)
    if collecting:
        sections.extend(current)
    return sections


def _find_sensitive(text: str, relative_path: str) -> list[dict]:
    found = []
    for kind, pattern in SENSITIVE_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append({"file": relative_path, "line": line, "kind": kind})
    return found


def _read_sources(source: Path) -> list[tuple[Path, str]]:
    if not source.is_dir():
        raise GuideExportError(f"学习资料目录不存在：{source}")
    files = sorted((p for p in source.glob("*.md") if p.name != "README.md"), key=lambda p: p.name)
    if len(files) != 14:
        raise GuideExportError(f"预期 14 篇 Markdown 课文，实际找到 {len(files)} 篇。")
    result = []
    for path in files:
        try:
            result.append((path, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError) as exc:
            raise GuideExportError(f"无法读取 {path.name}：{exc}") from exc
    return result


def export_guide(source_dir: str | os.PathLike, output_dir: str | os.PathLike, *, strict: bool = True) -> dict:
    """Export 14 chapters and report sensitive findings; strict mode blocks output."""
    source, output = Path(source_dir), Path(output_dir)
    sources = _read_sources(source)
    findings = []
    for path, text in sources:
        findings.extend(_find_sensitive(text, path.name))
    if findings and strict:
        detail = "\n".join(f"{f['file']}:{f['line']} [{f['kind']}]" for f in findings)
        raise GuideExportError("发现敏感内容，未导出：\n" + detail, findings)

    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    scratch = Path("D:/MentorFlow/.local/test-tmp/guide165")
    scratch.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".guide-export-", dir=scratch))
    backup = None
    try:
        if output.exists():
            if not output.is_dir():
                raise GuideExportError(f"导出目标不是目录：{output}")
            shutil.copytree(output, stage, dirs_exist_ok=True, copy_function=shutil.copy2, symlinks=True)
        (stage / "docs").mkdir(exist_ok=True)
        chapters = []
        for source_path, text in sources:
            cleaned = _strip_provenance(text)
            target = stage / "docs" / source_path.name
            target.write_text(cleaned, encoding="utf-8", newline="\n")
            chapters.append({"source": source_path.name, "path": f"docs/{source_path.name}",
                             "sha256": hashlib.sha256(cleaned.encode("utf-8")).hexdigest()})
        intro = ["# MentorFlow Guide", "", "MentorFlow 项目学习指南，按从零认识项目、理解架构与专题，再到口述自测的顺序阅读。", "",
                 "## 目录", ""]
        for chapter in chapters:
            title = next((line[2:].strip() for line in (stage / chapter["path"]).read_text(encoding="utf-8").splitlines() if line.startswith("# ")), chapter["source"])
            intro.append(f"- [{title}]({chapter['path']})")
        intro.extend(["", "## 在 MentorFlow 中使用", "", "在 MentorFlow 新建计划时，把本仓库的 GitHub 链接粘贴到资料来源，即可导入整套指南。", ""])
        (stage / "README.md").write_text("\n".join(intro), encoding="utf-8", newline="\n")
        citations = ["# 出处核对", "", "本文件用于核对课文出处。文件名以下划线开头，GitHub 导入器会跳过它。", ""]
        for source_path, text in sources:
            citations.extend([f"## {source_path.name}", "", *(_provenance_only(text) or ["出处表不存在。"]), ""])
        (stage / "_出处.md").write_text("\n".join(citations), encoding="utf-8", newline="\n")

        # Replace only after all inputs and generated files are complete.
        if output.exists():
            backup = scratch / f".{output.name}.backup-{uuid.uuid4().hex}"
            output.rename(backup)
        try:
            stage.rename(output)
        except Exception:
            if backup and backup.exists() and not output.exists():
                backup.rename(output)
            raise
        if backup and backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"output_dir": str(output), "chapter_count": len(chapters), "chapters": chapters,
            "readme_sha256": hashlib.sha256((output / "README.md").read_bytes()).hexdigest(),
            "citations_file": "_出处.md", "sensitive_findings": findings}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="导出 MentorFlow Guide Markdown 仓库")
    parser.add_argument("--source", default="docs/learn", help="源 Markdown 目录")
    parser.add_argument("--output", default=r"D:\MentorFlow\.local\guide-export\mentorflow-guide", help="导出目录")
    parser.add_argument("--strict", action="store_true", help="发现敏感内容时阻止导出（推送前使用）")
    args = parser.parse_args(argv)
    try:
        manifest = export_guide(args.source, args.output, strict=args.strict)
    except GuideExportError as exc:
        parser.exit(2, f"{exc}\n")
    if manifest["sensitive_findings"]:
        print("警告：发现敏感内容；本地样品已生成，发布前请清理并使用 --strict 重新导出。", file=sys.stderr)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
