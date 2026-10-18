from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
EXPORTER_PATH = ROOT / "scripts" / "learn" / "export_guide.py"
SPEC = importlib.util.spec_from_file_location("export_guide165", EXPORTER_PATH)
exporter = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(exporter)

from refactor.learning.course import github_repo


def make_source(path: Path, extra: str = "") -> Path:
    path.mkdir(parents=True)
    for index in range(14):
        provenance = "\n## 学习路线：本节出处\n\n| 编号 | 出处 |\n|---|---|\n| C01 | refactor/example.py:1 |\n"
        (path / f"{index:02d}-chapter-{index:02d}.md").write_text(
            f"# 第{index}章 测试章节\n\n正文内容保留。[C01]\n\n### 问题？\n\n- 答案要点\n{provenance}{extra if index == 0 else ''}",
            encoding="utf-8",
        )
    # Same named source README as the production directory must not become a chapter.
    (path / "README.md").write_text("# source index\n", encoding="utf-8")
    return path


def test_export_removes_provenance_and_preserves_lesson_and_questions(tmp_path):
    source = make_source(tmp_path / "source")
    output = tmp_path / "out"
    result = exporter.export_guide(source, output)
    lesson = (output / "docs" / "00-chapter-00.md").read_text(encoding="utf-8")
    assert result["chapter_count"] == 14
    assert "正文内容保留。" in lesson
    assert "答案要点" in lesson
    assert "学习路线：本节出处" not in lesson
    assert "refactor/example.py" not in lesson
    assert "[C01]" not in lesson
    assert "refactor/example.py" in (output / "_出处.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("sensitive", [
    "服务器地址 89.45.45.28", "服务器目录 /srv/mentorflow/data", "本机目录 D:\\MentorFlow\\private",
    "本机目录 D:/MentorFlow/private", "账号编号：MF-123456", "账号令牌 mfa1_1234567890abcdef",
    "联系 admin@example.com", "ghp_0123456789abcdefghijklmnopqrstuv",
    "api_key=AbCdEf0123456789abcdef",
])
def test_sensitive_content_blocks_without_partial_output(tmp_path, sensitive):
    source = make_source(tmp_path / "source", extra=f"\n{ sensitive }\n")
    output = tmp_path / "out"
    with pytest.raises(exporter.GuideExportError) as error:
        exporter.export_guide(source, output)
    assert error.value.findings
    assert not output.exists()
    assert "00-chapter-00.md:" in str(error.value)
    assert sensitive not in str(error.value)


def test_cli_default_warns_and_generates_local_sample_with_redacted_findings(tmp_path, capsys):
    source = make_source(tmp_path / "source", extra="\ncontact private@example.com\n")
    output = tmp_path / "out"
    assert exporter.main(["--source", str(source), "--output", str(output)]) == 0
    captured = capsys.readouterr()
    assert "本地样品已生成" in captured.err
    manifest = json.loads(captured.out)
    assert manifest["chapter_count"] == 14
    assert manifest["sensitive_findings"] == [
        {"file": "00-chapter-00.md", "line": 15, "kind": "email"}
    ]
    assert "private@example.com" not in captured.out + captured.err
    assert (output / "docs" / "00-chapter-00.md").exists()


def test_export_is_byte_for_byte_repeatable(tmp_path):
    source = make_source(tmp_path / "source")
    first, second = tmp_path / "first", tmp_path / "second"
    exporter.export_guide(source, first)
    exporter.export_guide(source, second)
    files = lambda root: {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert files(first) == files(second)


def test_reexport_preserves_git_metadata_and_unowned_files(tmp_path):
    source = make_source(tmp_path / "source")
    output = tmp_path / "out"
    output.mkdir()
    (output / ".git").mkdir()
    (output / ".git" / "config").write_text("repository metadata", encoding="utf-8")
    (output / "notes.txt").write_text("user file", encoding="utf-8")
    (output / "docs").mkdir()
    (output / "docs" / "custom.md").write_text("user chapter", encoding="utf-8")
    exporter.export_guide(source, output)
    assert (output / ".git" / "config").read_text(encoding="utf-8") == "repository metadata"
    assert (output / "notes.txt").read_text(encoding="utf-8") == "user file"
    assert (output / "docs" / "custom.md").read_text(encoding="utf-8") == "user chapter"


def test_same_level_heading_after_provenance_restores_question_and_keeps_ordinary_numbers():
    text = "正文编号 [3]。\n#### 学习路线：本题出处\n\n| C01 | origin |\n#### 后续问答\n\n答案保留。"
    cleaned = exporter._strip_provenance(text)
    assert "#### 学习路线：本题出处" not in cleaned
    assert "#### 后续问答" in cleaned
    assert "答案保留。" in cleaned
    assert "[3]" in cleaned
    assert "[ARCH-01]" not in exporter._strip_provenance("事实。[ARCH-01]")
    for variant in ("本节", "本题", "本篇"):
        assert "出处" not in exporter._strip_provenance(f"正文。\n### 学习路线：{variant}出处\n| C01 | origin |")


def test_importer_selects_all_fourteen_chapters_and_skips_index_and_provenance(tmp_path):
    source = make_source(tmp_path / "source")
    output = tmp_path / "out"
    exporter.export_guide(source, output)
    paths = [p.relative_to(output).as_posix() for p in output.rglob("*.md")]
    selected = github_repo.pick_files(paths)
    assert len(selected) == 14
    assert len(set(selected)) == 14
    assert all(path.startswith("docs/") for path in selected)
    assert "README.md" not in selected
    assert "_出处.md" not in selected
