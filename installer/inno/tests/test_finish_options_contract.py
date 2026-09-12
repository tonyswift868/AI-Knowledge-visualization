"""E109 source/layout contracts. No installer, app, registry write or GUI launch.

Real ISCC output-disabled compilation and offscreen font tests run separately.
Late finish tasks are applied explicitly; changing TasksList at wpFinished must
not be mistaken for re-running the already completed [Icons]/[Registry] sections.
"""
import re
import unittest

from test_directory_policy_source import ROOT, POLICY, SHELL, UI, body

OPTIONS = (ROOT / 'installer/inno/finish-options.iss').read_text(encoding='utf-8-sig')
GUIDE = (ROOT / 'docs/user/INSTALL_AND_UPDATE_GUIDE.zh-CN.md').read_text(encoding='utf-8')


def section(name):
    found = re.search(r'(?ms)^\[' + name + r'\]\n(.*?)(?=^\[|\Z)', SHELL)
    return found.group(1)


class FinishOptionsContract(unittest.TestCase):
    def test_progress_cancel_is_centered_at_all_virtual_scales_and_widths(self):
        page = body(UI, 'ShowBrandedPage')
        progress = page.split('end else if Installing then begin', 1)[1].split('end else begin', 1)[0]
        self.assertIn('SecondaryButton.Left := (BrandSurface.ClientWidth - SecondaryButton.Width) div 2;', progress)
        self.assertIn('SecondaryButton.Anchors := [akBottom];', progress)
        width = int(re.search(r'Bounds\(SecondaryButton, \d+, \d+, (\d+),', UI)[1])
        for scale in (1, 1.25, 1.5, 2):
            for client in (720, 900, 1024):
                w, c = round(width * scale), round(client * scale)
                self.assertLessEqual(abs(2 * ((c - w) // 2) + w - c), 1)
        self.assertIn("SecondaryButton.Caption := '取消安装';", progress)
        self.assertIn('WizardForm.CancelButton.OnClick(WizardForm.CancelButton);', UI)
        self.assertNotRegex(UI + SHELL, r'\.OnCloseQuery\s*:=|CancelButton\.OnClick\s*:=')
        self.assertIn('else EngineClose(Sender, Action);', body(UI, 'WizardClose'))

    def test_finish_is_the_only_options_page_both_defaults_off(self):
        tasks = section('Tasks')
        for task, label in [('desktopicon', '创建桌面快捷方式'), ('startup', '开机时启动 MentorFlow')]:
            self.assertRegex(tasks, 'Name: "' + task + '"; Description: "' + label + '"; Flags: unchecked checkablealone')
        self.assertEqual(tasks.count('Name:'), 2)
        self.assertIn('UsePreviousTasks=no', SHELL)
        self.assertIn('Result := PageID = wpSelectTasks;', body(SHELL, 'ShouldSkipPage'))
        page = body(UI, 'ShowBrandedPage')
        self.assertIn('WizardForm.TasksList.Visible := Finished;', page)
        self.assertIn('OptionsCaption.Visible := Finished;', page)
        self.assertIn("WizardSelectTasks('');", page)
        self.assertIn('if not FinishOptionsShown then begin', page)
        self.assertIn('WizardForm.TasksList.Parent := BrandSurface;', UI)
        self.assertNotIn('TNewCheckBox.Create', UI)  # Real native [Tasks] controls, no parallel state.

    def test_all_four_opt_in_combinations_have_separate_guards(self):
        apply = body(OPTIONS, 'ApplyFinishOptions')
        self.assertIn("if not WizardIsTaskSelected('desktopicon') and not WizardIsTaskSelected('startup') then Exit;", apply)
        desktop = apply.split("if WizardIsTaskSelected('desktopicon') then begin", 1)[1]
        desktop, startup = desktop.split("if WizardIsTaskSelected('startup') then begin", 1)
        self.assertIn('CopyFile(SourceLink, DesktopShortcutPath, True)', desktop)
        self.assertNotIn('RegWrite', desktop)
        self.assertIn("RegWriteStringValue(HKCU, StartupRunKey, '{#ShortcutName}', StartupCommand)", startup)
        self.assertNotIn('CopyFile', startup)
        # Each action appears only under its own real native task predicate.
        self.assertEqual(OPTIONS.count('CopyFile('), 1)
        self.assertEqual(OPTIONS.count('RegWriteStringValue('), 1)

    def test_desktop_is_native_icon_copy_not_an_alternate_launcher(self):
        self.assertIn('Name: "{autoprograms}\\{#ShortcutName}"; Filename: "{app}\\Updater\\UserRuntime.exe"; Parameters: "launch --root ""{app}"""', section('Icons'))
        self.assertIn("SourceLink := ExpandConstant('{autoprograms}\\{#ShortcutName}.lnk');", OPTIONS)
        self.assertIn("Result := ExpandConstant('{userdesktop}\\{#ShortcutName}.lnk');", OPTIONS)
        self.assertNotRegex(OPTIONS, r'CreateShellLink|ShellExec|Exec\(|powershell|cmd\.exe')
        self.assertIn('CopyFile(SourceLink, DesktopShortcutPath, True)', OPTIONS)
        self.assertIn('if not OwnsDesktopShortcut then begin', OPTIONS)
        self.assertIn('GetSHA256OfFile(DesktopShortcutPath)', body(OPTIONS, 'OwnsDesktopShortcut'))

    def test_startup_is_quoted_per_user_and_does_not_replace_foreign_value(self):
        self.assertIn("StartupRunKey = 'Software\\Microsoft\\Windows\\CurrentVersion\\Run';", OPTIONS)
        self.assertIn("AddQuotes(ExpandConstant('{app}\\Updater\\UserRuntime.exe'))", body(OPTIONS, 'StartupCommand'))
        self.assertIn("' launch --root ' + AddQuotes(ExpandConstant('{app}'))", body(OPTIONS, 'StartupCommand'))
        self.assertIn('if ExistingCommand <> StartupCommand then begin', OPTIONS)
        self.assertNotRegex(OPTIONS, r'\bHKLM\b|RegDeleteKey|StartupApproved|schtasks')
        self.assertIn('if IsAdminInstallMode or IsAdmin then begin', OPTIONS)

    def test_options_apply_only_on_explicit_finish_before_native_launch_or_close(self):
        for handler in ('PrimaryClick', 'SecondaryClick'):
            code = body(UI, handler)
            self.assertLess(code.index('if WizardForm.CurPageID = wpFinished then begin'), code.index('ApplyFinishOptions'))
            self.assertLess(code.index('ApplyFinishOptions'), code.index('EngineNext('))
            self.assertIn("if OptionsFeedback.Caption <> '' then Exit;", code)
        self.assertEqual(UI.count('ApplyFinishOptions'), 2)
        self.assertNotIn('ApplyFinishOptions', SHELL)
        self.assertNotIn('DeinitializeSetup', SHELL)

    def test_launch_is_a_button_using_original_user_not_a_checkbox(self):
        self.assertIn("PaintPrimary('启动 MentorFlow')", UI)
        self.assertIn("SecondaryButton.Caption := '关闭'", UI)
        self.assertNotIn('启动 MentorFlow', section('Tasks').replace('开机时启动 MentorFlow', ''))
        run = section('Run')
        self.assertIn('nowait postinstall skipifsilent runasoriginaluser unchecked', run)
        self.assertIn('PrivilegesRequired=lowest', SHELL)
        self.assertNotIn('runascurrentuser', run)
        self.assertIn('WizardForm.RunList.Checked[0] := True;', body(UI, 'PrimaryClick'))
        self.assertIn('WizardForm.RunList.Checked[0] := False;', body(UI, 'SecondaryClick'))

    def test_already_elevated_caller_cannot_launch_or_apply_user_options(self):
        self.assertIn('Result := not IsAdmin;', body(OPTIONS, 'CanLaunchMentorFlow'))
        self.assertIn('Check: CanLaunchMentorFlow', section('Run'))
        launch = body(UI, 'PrimaryClick')
        self.assertLess(launch.index('if not CanLaunchMentorFlow then begin'), launch.index('ApplyFinishOptions'))
        self.assertIn('Exit;', launch[launch.index('if not CanLaunchMentorFlow then begin'):launch.index('ApplyFinishOptions')])
        self.assertNotIn('CanLaunchMentorFlow', body(UI, 'SecondaryClick'))  # Close remains available.
        self.assertIn('if WizardForm.RunList.Items.Count > 0 then WizardForm.RunList.Checked[0] := False;',
                      body(UI, 'SecondaryClick'))  # A false Run Check removes the native item.
        self.assertLess(launch.index('if WizardForm.RunList.Items.Count = 0 then begin'),
                        launch.index('WizardForm.RunList.Checked[0] := True;'))
        apply = body(OPTIONS, 'ApplyFinishOptions')
        self.assertLess(apply.index('if IsAdminInstallMode or IsAdmin then begin'), apply.index('CopyFile('))
        self.assertLess(apply.index('if IsAdminInstallMode or IsAdmin then begin'), apply.index('RegWriteStringValue('))

    def test_three_fixed_start_menu_entries_use_authorized_public_contract(self):
        icons = section('Icons')
        self.assertEqual(icons.count('Name:'), 3)
        self.assertIn('Name: "{autoprograms}\\{#ShortcutName} 修复与更新"; Filename: "{app}\\Updater\\UserRuntime.exe"; Parameters: "repair-update --root ""{app}"""; WorkingDir: "{app}"', icons)
        self.assertIn('Name: "{autoprograms}\\卸载 {#ShortcutName}"; Filename: "{uninstallexe}"', icons)
        self.assertNotRegex(icons, r'(?i)SelfCheck|UpdateBridge|Python|PowerShell|cmd\.exe')

    def test_uninstall_removes_only_matching_owned_shortcut_and_run_value(self):
        cleanup = body(OPTIONS, 'RemoveOwnedFinishOptions')
        self.assertIn('if OwnsDesktopShortcut then DeleteFile(DesktopShortcutPath);', cleanup)
        self.assertIn('if RecordedCommand <> StartupCommand then Exit;', cleanup)
        self.assertIn("if ExistingCommand = RecordedCommand then RegDeleteValue(HKCU, StartupRunKey, '{#ShortcutName}');", cleanup)
        uninstall = body(SHELL, 'CurUninstallStepChanged')
        self.assertLess(uninstall.index('RaiseException('), uninstall.index('RemoveOwnedFinishOptions;'))
        self.assertLess(body(SHELL, 'PrepareToInstall').index('CapturePreviousFinishOptions'),
                        body(SHELL, 'PrepareToInstall').index('InstallThroughCore(Target)'))
        self.assertIn('RestorePreviousFinishOptions;', body(SHELL, 'CurStepChanged'))
        restore = body(OPTIONS, 'RestorePreviousFinishOptions')
        self.assertNotRegex(restore, r'RegWrite|CopyFile|CreateShellLink')

    def test_finish_inventory_is_outside_signed_app_and_namespaced(self):
        self.assertIn("{app}\\Maintenance\\finish-options.ini", body(OPTIONS, 'FinishInventory'))
        self.assertNotIn('.mentorflow-install.ini', OPTIONS)
        self.assertIn("'Maintenance\\finish-options.ini'", body(OPTIONS, 'CapturePreviousFinishOptions'))
        self.assertIn('Type: files; Name: "{app}\\Maintenance\\finish-options.ini"', section('UninstallDelete'))
        self.assertNotIn("StartupRunKey, 'MentorFlow'", OPTIONS)

    def test_guide_has_only_allowed_user_topics_and_no_internal_details(self):
        for phrase in ('选择其他位置', 'E:\\MentorFlow', 'E:\\Apps\\MentorFlow', '检查更新',
                       '所选文件夹里可以有其他软件',
                       '更新到最新版本', '未登录', '登录页', 'MentorFlow 修复与更新',
                       '不需要重新下载安装包', '卸载 MentorFlow', '程序目录和用户数据目录是分开的'):
            self.assertIn(phrase, GUIDE)
        self.assertNotRegex(GUIDE, r'(?i)manifest|sha256|hash|receipt|https?://|服务器|\.exe|PowerShell|Python|UpdateBridge|SelfCheck')
        self.assertEqual(list((ROOT / 'docs/user').glob('*.md')),
                         [ROOT / 'docs/user/INSTALL_AND_UPDATE_GUIDE.zh-CN.md'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
