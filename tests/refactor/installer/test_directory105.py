"""Guard the custom location page's use of Inno's AppId-scoped directory.

Actual Inno execution (including stale/foreign registry records) is exercised
by scripts/release/installer105, not by a Python reimplementation of Pascal.
"""
from pathlib import Path

INNO = Path(__file__).resolve().parents[3] / 'refactor/desktop/packaging/inno'


def test_directory105_engine_remembers_same_appid():
    setup = (INNO/'MentorFlow.iss').read_text(encoding='utf-8')
    assert 'UsePreviousAppDir=yes' in setup
    assert 'AppId={#ProductAppId}' in setup
    assert r'DefaultDirName={localappdata}\Programs\MentorFlow' in setup
    assert 'SelectedParent := ExistingKnownParent;' in setup


def test_directory105_previous_must_be_recognized_not_merely_present():
    policy = (INNO/'directory-policy.iss').read_text(encoding='utf-8')
    previous = policy.split('function ExistingKnownParent: String;')[1].split('function Writable')[0]
    assert 'WizardDirValue' in previous
    assert "ExtractFileName(Previous), 'MentorFlow'" in previous
    for evidence in ("'App'", "'Data'", r"'App\MentorFlow.exe'"):
        assert evidence in previous
    assert 'ClassifyTarget(Previous) = tkUpgrade' in previous
    assert 'ClassifyTarget(Previous) = tkRepair' in previous
    assert previous.index('Exit;') < previous.index(r"ExpandConstant('{localappdata}\Programs')")
    # No search for unrelated AppIds, no independently maintained registry lookup.
    assert 'RegQuery' not in previous


def test_directory105_keeps_core_admission_and_user_override():
    setup = (INNO/'MentorFlow.iss').read_text(encoding='utf-8')
    brand = (INNO/'branded-pages.iss').read_text(encoding='utf-8')
    assert 'ValidateParent(SelectedParent, Target, NeedsAdmin, True)' in setup
    assert 'InstallThroughCore(Target)' in setup
    assert 'SelectedParent := Chosen;' in brand
    assert 'PathCaption.ReadOnly := True;' in brand
    assert 'PathCaption.Text := Target;' in brand  # full UIA ValuePattern value
    assert "ModeCaption.Caption := '已安装，可更新应用'" in brand
