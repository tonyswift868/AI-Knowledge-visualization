"""Focused production-source contracts; never launch an installer or Windows UI.

These prove the source regression and compile separately with real ISCC. They
explicitly do NOT claim PASS_REAL_INSTALLER for a physical title-bar X click.
"""
import ntpath
import re
import unittest

from test_directory_policy_source import POLICY, SHELL, UI, ROOT, body


class ParentAndCloseContract(unittest.TestCase):
    def test_picker_result_is_parent_and_append_uses_exact_product_component(self):
        picker = body(UI, 'BrowseParent')
        self.assertIn('Chosen := SelectedParent;', picker)
        self.assertIn('BrowseForFolder(', picker)
        self.assertIn('SelectedParent := Chosen;', picker)
        self.assertIn('RefreshLocation(nil);', picker)
        append = re.findall(r"Target := AddBackslash\(Parent\) \+ '([^']+)'", POLICY)
        self.assertEqual(append, ['MentorFlow'])
        # Evaluate the actual literal appended by production, not a second name.
        for parent, expected in [('E:\\', 'E:\\MentorFlow'),
                                 ('E:\\Apps', 'E:\\Apps\\MentorFlow'),
                                 ('E:\\Software', 'E:\\Software\\MentorFlow'),
                                 ('E:\\Software\\', 'E:\\Software\\MentorFlow')]:
            with self.subTest(parent=parent):
                self.assertEqual(ntpath.join(parent, append[0]), expected)
        self.assertIn("BrowseButton.Caption := '选择其他位置';", UI)
        self.assertIn("PageTitle.Caption := '选择安装位置';", UI)

    def test_missing_child_is_new_before_inventory_or_unknown_error(self):
        classify = body(POLICY, 'ClassifyTarget')
        self.assertIn('if not DirExists(Target) then begin Result := tkNew; Exit; end;', classify)
        self.assertLess(classify.index('Result := tkUnknown;'), classify.index('if FileExists(Target)'))
        self.assertIn('UI hints only, never install authority', classify)
        self.assertIn('InstallThroughCore(Target)', body(SHELL, 'PrepareToInstall'))
        validate = body(POLICY, 'ValidateParent')
        self.assertIn('if ValidatedTargetKind = tkUnknown then begin', validate)

    def test_empty_and_valid_existing_branches_remain_available(self):
        classify = body(POLICY, 'ClassifyTarget')
        self.assertIn("if KnownTree(Target, '', '', False) then Result := tkNew", classify)
        self.assertIn('then Result := tkRepair', classify)
        self.assertIn('Result := tkUpgrade;', classify)
        for name in ('per-user-install.json', 'current-release.json', 'highest-release.json', 'per-user-transaction.json'):
            self.assertIn(name, classify)
        self.assertNotIn('CreateDir', classify)

    def test_close_native_gate_remains_focusable_but_covered_not_tabbed(self):
        # Inno 7.1 Setup.MainForm.pas TMainForm.Close gates on
        # WizardForm.CancelButton.CanFocus (visible/enabled ancestor chain).
        self.assertNotRegex(UI + SHELL, r'CancelButton\.(?:Visible|Enabled) := False')
        self.assertNotRegex(UI + SHELL, r'CancelButton\.Parent\s*:=')
        branded = body(UI, 'ShowBrandedPage')
        self.assertIn('WizardForm.CancelButton.TabStop := False;', branded)
        self.assertIn('BrandSurface.BringToFront;', branded)
        create = body(UI, 'CreateBrandedPages')
        self.assertIn('BrandSurface.Parent := WizardForm;', create)
        self.assertIn('BrandSurface.Align := alClient;', create)
        self.assertIn('BrandSurface.ParentBackground := False;', create)

    def test_location_has_no_secondary_button_but_progress_can_cancel(self):
        branded = body(UI, 'ShowBrandedPage')
        self.assertIn('SecondaryButton.Visible := not Location;', branded)
        self.assertIn("SecondaryButton.Caption := '取消安装';", branded)
        self.assertIn('PrimaryButton.Visible := not Installing;', branded)
        self.assertIn('BrowseButton.Visible := Location;', branded)

    def test_x_and_progress_use_one_native_cancel_route(self):
        self.assertIn('AllowCancelDuringInstall=yes', SHELL)
        builder = (ROOT / 'scripts/build_inno_installer.py').read_text(encoding='utf-8')
        self.assertNotIn('/NOCANCEL', (SHELL + UI + builder).upper())
        self.assertNotRegex(UI + SHELL, r'(?i)\.OnCloseQuery\s*:=|CancelButton\.OnClick\s*:=')
        close = body(UI, 'WizardClose')
        self.assertIn('if WizardForm.CurPageID = wpFinished then begin', close)
        self.assertIn('SecondaryClick(Sender);', close)
        self.assertIn('else EngineClose(Sender, Action);', close)
        secondary = body(UI, 'SecondaryClick')
        self.assertIn('else if WizardForm.CancelButton.Enabled then', secondary)
        self.assertIn('WizardForm.CancelButton.OnClick(WizardForm.CancelButton)', secondary)
        cancel = body(SHELL, 'CancelButtonClick')
        self.assertIn('if CurPageID = LocationPage.ID then Confirm := False;', cancel)
        self.assertIn('if CoreRunning then begin', cancel)
        self.assertIn('Cancel := False;', cancel)
        self.assertIn("SaveStringToFile(CoreCancelFile, 'cancel', False);", cancel)
        self.assertIn('Confirm := False;', cancel)
        self.assertNotRegex(cancel, r'Confirm := True|MsgBox|RunList|NextButton|ssPostInstall')
        # Active signed-core work receives a cancellation request, not fake success.
        self.assertNotRegex(UI + SHELL, r'(?i)MsgBox\(|TaskDialogMsgBox\(|NeedToAbortInstall\s*:=')

    def test_finish_x_is_enabled_and_uses_existing_close_without_launch(self):
        # Inno disables SC_CLOSE at wpFinished, and MainForm.Close only cancels
        # ssPreInstall/ssInstall. Visibility alone cannot make finish X work.
        create = body(UI, 'CreateBrandedPages')
        self.assertIn('EngineClose := WizardForm.OnClose;', create)
        self.assertIn('WizardForm.OnClose := @WizardClose;', create)
        branded = body(UI, 'ShowBrandedPage')
        finish = branded[branded.index("PageTitle.Caption := 'MentorFlow 已安装';"):]
        self.assertIn('EnableWizardMenuItem(WizardSystemMenu(WizardForm.Handle, False), $F060, 0);', finish)
        close = body(UI, 'WizardClose')
        self.assertIn('Action := caNone;', close)
        self.assertNotIn('PrimaryClick', close)
        secondary = body(UI, 'SecondaryClick')
        self.assertLess(secondary.index('WizardForm.RunList.Checked[0] := False;'),
                        secondary.index('EngineNext(WizardForm.NextButton);'))

    def test_preinstall_close_cannot_create_target_or_install_receipt(self):
        for text, names in [(SHELL, ['InitializeWizard']),
                            (UI, ['ShowBrandedPage', 'RefreshLocation', 'SecondaryClick'])]:
            for name in names:
                self.assertNotRegex(body(text, name),
                    r'CreateDir\(|ForceDirectories\(|SetIniString\(|SaveStringToFile\(|PrepareToInstall\(')
        cancel = body(SHELL, 'CancelButtonClick')
        self.assertNotRegex(cancel.split('Exit;', 1)[1], r'CreateDir\(|SetIniString\(|SaveStringToFile\(')
        self.assertNotRegex(SHELL + UI + POLICY, r'\bCreateDir\(')
        core = (ROOT / 'installer/inno/per-user-core.iss').read_text(encoding='utf-8')
        self.assertLess(core.index("'preflight --root '"), core.index("'install --root '"))


if __name__ == '__main__':
    unittest.main(verbosity=2)
