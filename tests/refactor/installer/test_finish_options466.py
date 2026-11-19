"""BUG-293: finish-page optional settings never trap the installer."""

from pathlib import Path


INNO = Path(__file__).resolve().parents[3] / "refactor/desktop/packaging/inno"


def test_finish466_previews_conflicts_and_compares_launch_contract():
    finish = (INNO / "finish-options.iss").read_text(encoding="utf-8")
    preview = finish.split("function PreviewFinishOptions: String;", 1)[1].split(
        "procedure CapturePreviousFinishOptions", 1
    )[0]

    assert "DesktopShortcutMatches" in preview
    assert "DeselectFinishTask('desktopicon')" in preview
    assert "DeselectFinishTask('startup')" in preview
    assert "桌面已有同名快捷方式，保留原样" in preview
    assert "已有不同的开机启动设置，保留原样" in preview

    matcher = finish.split("function DesktopShortcutMatches", 1)[1].split(
        "procedure AppendFinishMessage", 1
    )[0]
    assert "TargetPath" in matcher and "Arguments" in matcher


def test_finish466_failures_are_logged_without_blocking_any_exit_path():
    finish = (INNO / "finish-options.iss").read_text(encoding="utf-8")
    apply = finish.split("function ApplyFinishOptions: String;", 1)[1].split(
        "procedure RemoveOwnedFinishOptions", 1
    )[0]
    pages = (INNO / "branded-pages.iss").read_text(encoding="utf-8")

    assert "请取消勾选后继续" not in apply
    assert "AppendFinishMessage" in apply and "Log('Finish options:" in finish
    assert "CopyFile(SourceLink, DesktopShortcutPath, True)" in apply
    assert "CompareText(ExistingCommand, StartupCommand)" in apply

    for proc in ("procedure PrimaryClick", "procedure SecondaryClick", "procedure WizardClose"):
        body = pages.split(proc, 1)[1].split("procedure ", 1)[0]
        assert "if OptionsFeedback.Caption <> '' then Exit" not in body
    assert "OptionsFeedback.Caption := PreviewFinishOptions" in pages
    assert "if WizardForm.RunList.Items.Count > 0 then" in pages
