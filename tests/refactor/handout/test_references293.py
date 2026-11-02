from pathlib import Path

from scripts.handout.references import build_references


def test_reference_uses_section_owner_and_keeps_line_evidence(tmp_path: Path) -> None:
    docs = tmp_path / "docs" / "project"
    docs.mkdir(parents=True)
    (docs / "CURRENT_STATE.md").write_text(
        "# State\n\n### [ARCH-01] UI\n\n"
        "Implementation is `refactor/learning/app/window.py`; see [BUG-12].\n\n"
        "### [ARCH-02] Other\n\nNo related file.\n",
        encoding="utf-8",
    )

    refs = build_references(
        tmp_path,
        ["refactor/learning/app/window.py"],
        ["docs/project/CURRENT_STATE.md"],
        ["main.py", "index.ts"],
    )

    rows = refs["refactor/learning/app/window.py"]
    assert [row["id"] for row in rows] == ["ARCH-01", "BUG-12"]
    assert [row["relation"] for row in rows] == ["section", "inline"]
    assert all(row["owner"] == "ARCH-01" for row in rows)
    assert rows[0]["document"] == "docs/project/CURRENT_STATE.md"
    assert rows[0]["line"] == 5
    assert "refactor/learning/app/window.py" in rows[0]["match"]


def test_common_basename_requires_full_path(tmp_path: Path) -> None:
    doc = tmp_path / "docs" / "project"
    doc.mkdir(parents=True)
    (doc / "BUGS.md").write_text(
        "### [BUG-1] generic\n\nThe main.py file does something.\n\n"
        "### [BUG-2] precise\n\n`refactor/learning/app/main.py` changed.\n",
        encoding="utf-8",
    )

    refs = build_references(
        tmp_path,
        ["refactor/learning/app/main.py"],
        ["docs/project/BUGS.md"],
        ["main.py"],
    )

    rows = refs["refactor/learning/app/main.py"]
    assert [row["id"] for row in rows] == ["BUG-2"]


def test_basename_match_for_distinctive_file_and_no_match(tmp_path: Path) -> None:
    doc = tmp_path / "docs" / "project"
    doc.mkdir(parents=True)
    (doc / "CURRENT_STATE.md").write_text(
        "### [PLAN-01] Uses importer\n\nThe course importer.py parses input.\n",
        encoding="utf-8",
    )

    refs = build_references(
        tmp_path,
        ["refactor/learning/course/importer.py", "refactor/learning/course/unmentioned.py"],
        ["docs/project/CURRENT_STATE.md"],
        ["main.py"],
    )

    assert [row["id"] for row in refs["refactor/learning/course/importer.py"]] == ["PLAN-01"]
    assert refs["refactor/learning/course/unmentioned.py"] == []


def test_repeated_basename_requires_full_path(tmp_path: Path) -> None:
    doc = tmp_path / "docs" / "project"
    doc.mkdir(parents=True)
    (doc / "CURRENT_STATE.md").write_text(
        "### [ARCH-03] Storage\n\nThe storage.py module is used here.\n",
        encoding="utf-8",
    )

    refs = build_references(
        tmp_path,
        ["refactor/learning/chat/storage.py", "refactor/learning/course/storage.py"],
        ["docs/project/CURRENT_STATE.md"],
        [],
    )

    assert refs["refactor/learning/chat/storage.py"] == []
    assert refs["refactor/learning/course/storage.py"] == []
