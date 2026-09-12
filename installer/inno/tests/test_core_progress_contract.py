"""Production adapter contracts; no installer or native window is launched."""
import unittest

from test_directory_policy_source import ROOT, SHELL, UI, body

CORE = (ROOT / 'installer/inno/per-user-core.iss').read_text(encoding='utf-8-sig')


class CoreProgressContract(unittest.TestCase):
    def test_core_percentage_uses_same_scale_as_inno_progress(self):
        progress = body(CORE, 'CoreProgress')
        # The core publishes percent, whereas the shared bar has 1,000 steps.
        # Both producers must go through the same renderer so 100% fills it.
        self.assertIn("GetIniInt('install', 'percent', 0, 0, 100, CoreStatus)", progress)
        self.assertIn('UpdateBrandedProgress(Percent, 100);', progress)
        self.assertNotIn('CopyProgress.Position :=', progress)
        self.assertIn('CopyProgress.Max := 1000;', UI)
        self.assertIn('(Current * 1000.0) / Maximum', body(UI, 'UpdateBrandedProgress'))

    def test_prepare_callback_enables_native_cancel_only_while_core_is_owned(self):
        install = body(CORE, 'InstallThroughCore')
        self.assertIn('CancelWasEnabled := WizardForm.CancelButton.Enabled;', install)
        active = install.index('CoreRunning := True;')
        enable = install.index('WizardForm.CancelButton.Enabled := True;')
        show = install.index('ShowBrandedPage(wpInstalling);')
        execute = install.index('Exec(Runtime, Parameters,')
        restore = install.index('WizardForm.CancelButton.Enabled := CancelWasEnabled;')
        self.assertLess(active, enable)
        self.assertLess(enable, show)
        self.assertLess(show, execute)
        self.assertLess(execute, restore)
        self.assertIn('finally', install[execute:restore])
        self.assertIn('CoreRunning := False;', install[execute:restore])
        # Enabling the native button must not replace its safe cancellation route.
        cancel = body(SHELL, 'CancelButtonClick')
        self.assertIn('Cancel := False;', cancel)
        self.assertIn("SaveStringToFile(CoreCancelFile, 'cancel', False);", cancel)
        self.assertNotRegex(CORE, r'(?i)TerminateProcess|taskkill|\.OnClose\w*\s*:=')

    def test_core_errors_remain_diagnostics_not_product_copy(self):
        install = body(CORE, 'InstallThroughCore')
        self.assertNotIn("GetIniString('install', 'error'", install)
        self.assertIn('if CoreCancelRequested then', install)
        self.assertIn('安装已取消', install)
        self.assertIn('安装未完成', install)


if __name__ == '__main__':
    unittest.main(verbosity=2)
