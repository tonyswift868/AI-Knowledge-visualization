"""Source-freeze invariants, not a claim of filesystem runtime execution.

The old UXPROBE executable path is retired with the alternate fixture build.
These checks inspect the actual Pascal policy; real ISCC compilation is separate.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
POLICY = (ROOT / 'installer/inno/directory-policy.iss').read_text(encoding='utf-8-sig')
SHELL = (ROOT / 'installer/inno/MentorFlow.iss').read_text(encoding='utf-8-sig')
UI = (ROOT / 'installer/inno/branded-pages.iss').read_text(encoding='utf-8-sig')


def body(text, name):
    found = re.search(r'(?mi)^(?:function|procedure) ' + name + r'\b', text)
    if not found:
        raise AssertionError('Missing production function ' + name)
    end = re.search(r'(?mi)^(?:function|procedure) ', text[found.end():])
    return text[found.start():found.end() + end.start()] if end else text[found.start():]


class DirectorySourceContract(unittest.TestCase):
    def test_only_existing_known_folders_can_be_default_parent(self):
        default = body(POLICY, 'ExistingKnownParent')
        self.assertEqual(re.findall(r"ExpandConstant\('([^']+)'\)", default),
                         ['{localappdata}', '{userappdata}', '{userdocs}'])
        self.assertEqual(default.count('if DirExists(Result) then Exit'), 3)
        self.assertIn("Result := '';", default)
        self.assertIn('SelectedParent := ExistingKnownParent;', SHELL)
        self.assertNotIn('{param:', SHELL + POLICY + UI)
        self.assertNotRegex(SHELL + POLICY + UI, r'(?i)MF14|MFUAC|CodexScratch|E:\\Codex')

    def test_missing_drive_and_parent_fail_before_target_creation(self):
        validate = body(POLICY, 'ValidateParent')
        drive = validate.index('if not DirExists(Copy(Parent, 1, 3)) then begin')
        parent = validate.index('if not DirExists(Parent) then begin')
        target = validate.index("Target := AddBackslash(Parent) + 'MentorFlow'")
        self.assertLess(validate.index('if Length(Parent) < 3 then begin'), validate.index("Parent[2] <> ':'"))
        self.assertLess(drive, parent)
        self.assertLess(parent, target)
        self.assertIn('Exit;', validate[drive:parent])
        self.assertIn('Exit;', validate[parent:target])

    def test_only_final_application_component_is_created_after_revalidation(self):
        self.assertNotRegex(SHELL + POLICY + UI, r'\bCreateDir\(')
        prepare = body(SHELL, 'PrepareToInstall')
        self.assertLess(prepare.index('ValidateParent('), prepare.index('InstallThroughCore(Target)'))
        self.assertLess(prepare.index('CompareText(Target, WizardDirValue)'), prepare.index('InstallThroughCore(Target)'))
        self.assertNotRegex(SHELL + POLICY + UI, r'(?i)ForceDirectories\(|DeleteFile\(Target|DelTree\(')
        core = (ROOT / 'installer/inno/per-user-core.iss').read_text(encoding='utf-8')
        self.assertLess(core.index("'preflight --root '"), core.index("'install --root '"))
        self.assertIn("' --release '", core)

    def test_unknown_nonempty_target_is_rejected_without_deletion(self):
        classify = body(POLICY, 'ClassifyTarget')
        self.assertIn('Result := tkUnknown;', classify)
        self.assertIn('if FileExists(Target) then Exit', classify)
        tree = body(POLICY, 'KnownTree')
        self.assertIn('if not Known then begin Result := False; Exit; end', tree)
        self.assertIn("if KnownTree(Target, '', '', False) then Result := tkNew", classify)
        self.assertIn('if ValidatedTargetKind = tkUnknown then begin', POLICY)
        self.assertNotIn('DeleteFile(', classify)

    def test_existing_install_requires_identity_before_repair_or_upgrade(self):
        classify = body(POLICY, 'ClassifyTarget')
        self.assertIn('UI hints only, never install authority', classify)
        self.assertIn("'per-user-install.json'", classify)
        self.assertIn("'current-release.json'", classify)
        self.assertIn("'highest-release.json'", classify)
        self.assertIn("'per-user-transaction.json'", classify)
        self.assertIn("'{#PayloadExeSHA256}'", classify)
        self.assertIn('Result := tkRepair', classify)
        self.assertIn('Result := tkUpgrade', classify)
        self.assertIn("if Result = '' then Result := InstallThroughCore(Target)", SHELL)

    def test_repair_after_upgrade_remains_owned_by_signed_core(self):
        self.assertNotIn('CapturePreviousOwnership', SHELL + POLICY)
        self.assertNotIn('PreservePreviousOwnedPaths', SHELL + POLICY)
        self.assertNotIn('DestName: ".mentorflow-install.ini"', SHELL)
        self.assertIn('DestDir: "{tmp}\\payload"; Flags: dontcopy', SHELL)
        self.assertIn('DestDir: "{app}\\Maintenance"', SHELL)
        self.assertIn("'uninstall --root '", SHELL)

    def test_reparse_and_enumeration_failures_remain_fail_closed(self):
        self.assertIn('(Attributes and $400) <> 0', body(POLICY, 'SafeAncestors'))
        self.assertIn('(Find.Attributes and $400) <> 0', body(POLICY, 'KnownTree'))
        self.assertIn('Result := LastFileError = 2', POLICY)
        self.assertIn('Result := LastFileError = 18', POLICY)

    def test_sufficient_space_path_has_no_dialog_or_debug_ui(self):
        self.assertIn('Free, Total, Required: Int64', POLICY)
        self.assertIn('if Free < Required then begin', POLICY)
        self.assertIn('HumanSize(Required)', POLICY)
        self.assertNotRegex(SHELL + POLICY + UI, r'(?i)MsgBox\(|TaskDialogMsgBox\(')
        self.assertIn('DirExistsWarning=no', SHELL)
        self.assertIn('DisableStartupPrompt=yes', SHELL)
        self.assertNotIn('DetailsButton', UI)

    def test_shortened_path_display_never_changes_validated_destination(self):
        self.assertIn('MinimizePathName(Target, PathCaption.Font, PathCaption.Width)', UI)
        self.assertIn('PathCaption.Hint := Target', UI)
        self.assertIn('WizardForm.DirEdit.Text := Target', SHELL)
        self.assertNotIn('WizardForm.DirEdit.Text := PathCaption.Caption', SHELL)


if __name__ == '__main__':
    unittest.main(verbosity=2)
