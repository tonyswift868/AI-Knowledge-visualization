"""Offline source/layout checks, NOT a substitute for native screenshot review.

No installer execution, user settings, Windows UI or product-data access.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
SHELL = (ROOT / 'installer/inno/MentorFlow.iss').read_text(encoding='utf-8-sig')
UI = (ROOT / 'installer/inno/branded-pages.iss').read_text(encoding='utf-8-sig')


def luminance(color):
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return .2126*r + .7152*g + .0722*b


class DesignContract(unittest.TestCase):
    def test_official_dynamic_style_and_no_legacy_sidebar(self):
        self.assertIn('WizardStyle=modern {#Appearance} windows11 hidebevels includetitlebar', SHELL)
        self.assertIn('WizardImageFile=\n', SHELL)
        self.assertIn('WizardImageFileDynamicDark=\n', SHELL)
        self.assertIn("LocationPage := CreateCustomPage(", SHELL)
        self.assertNotIn('CreateInputDirPage(', SHELL)
        self.assertIn('DisableWelcomePage=yes', SHELL)
        self.assertIn('WizardSmallImageFile={#BuildInput}\\MentorFlow.png', SHELL)

    def test_authoritative_literal_bounds_at_virtual_dpi(self):
        # Actual source constants, not a second manually maintained layout table.
        rectangles = re.findall(r'Bounds\((\w+), (\d+), (\d+), (\d+), (\d+)\)', UI)
        rectangles += re.findall(r"(\w+) := LabelAt\([^\n]+?, (\d+), (\d+), (\d+), (\d+), \d+,", UI)
        self.assertGreaterEqual(len(rectangles), 10)
        for scale in (1, 1.25, 1.5, 2):
            with self.subTest(scale=scale):
                for name, x, y, w, h in rectangles:
                    x, y, w, h = [round(int(v)*scale) for v in (x, y, w, h)]
                    self.assertGreater(w, 0, name)
                    self.assertGreater(h, 0, name)
                    self.assertLessEqual(x + w, round(720*scale), name)
                    self.assertLessEqual(y + h, round(452*scale), name)
                bounds = {name: tuple(int(v) for v in (x,y,w,h)) for name,x,y,w,h in rectangles}
                left = bounds['SecondaryButton']
                right = bounds['PrimaryButton']
                self.assertGreater(right[0], left[0]+left[2])
                path = bounds['PathCaption']
                browse = bounds['BrowseButton']
                self.assertGreater(browse[0], path[0]+path[2])
                brand = bounds['BrandName']
                tagline = bounds['BrandTagline']
                self.assertGreater(tagline[1], brand[1]+brand[3])
        self.assertIn('MulDiv(Value, WizardForm.Font.PixelsPerInch, 96)', UI)

    def test_light_dark_text_and_primary_contrast(self):
        pairs = [('2f332f', 'fffdf8'), ('6b665f', 'fffdf8'), ('6b665f', 'f5f0e7'),
                 ('ffffff', 'a94f79'), ('f8f1f5', '242127'), ('c6bac2', '242127'),
                 ('c6bac2', '302c33'), ('30232b', 'f3aecf')]
        for fg, bg in pairs:
            with self.subTest(fg=fg, bg=bg):
                for color in (fg, bg):
                    if color != 'ffffff':
                        self.assertIn(f"StrToColor('#{color}')", UI)
                light, dark = sorted([luminance('#'+fg), luminance('#'+bg)], reverse=True)
                self.assertGreaterEqual((light+.05)/(dark+.05), 4.5)
        self.assertIn('if HighContrastActive then begin', UI)

    def test_progress_driven_only_by_engine_callback(self):
        self.assertIn('UpdateBrandedProgress(CurProgress, MaxProgress)', SHELL)
        self.assertIn('(Current * 100.0) / Maximum', UI)
        self.assertIn('CopyProgress.Position := Percent', UI)
        self.assertNotRegex(UI, r'(?i)SetTimer|Sleep\(|random\(')
        self.assertIn('DetailMemo.Visible := False', UI)
        self.assertIn('WizardForm.FilenameLabel.Visible := False', UI)
        self.assertIn('DetailMemo.Lines.Count >= 100', UI)

    def test_finish_launch_is_explicit_close_does_not_launch(self):
        self.assertIn('runasoriginaluser unchecked', SHELL)
        self.assertIn("PaintPrimary('启动 MentorFlow')", UI)
        self.assertIn('WizardForm.RunList.Checked[0] := True', UI)
        self.assertIn('WizardForm.RunList.Checked[0] := False', UI)
        self.assertIn("SecondaryButton.Caption := '关闭'", UI)

    def test_no_auto_elevation_host_settings_or_implicit_parent_creation(self):
        self.assertIn("Chosen, False)", UI)
        self.assertNotIn("ShellExec('runas'", SHELL + UI)
        self.assertNotRegex(SHELL + UI, r'(?i)AppsUseLightTheme|SetProcessDpi|RegWrite|DisplayConfig')
        self.assertIn("if not CreateDir(Target)", SHELL)
        self.assertNotIn('ForceDirectories(', SHELL + UI)
        self.assertIn('SetupMutex=MentorFlow.Installer', SHELL)

    def test_brand_source_is_not_repainted(self):
        builder = (ROOT / 'scripts/build_inno_installer.py').read_text(encoding='utf-8')
        self.assertIn("frontend/public/teacher2d/tsubaki-live2d/icon.png", builder)
        self.assertIn("shutil.copyfile(source, stage / 'MentorFlow.png')", builder)
        for incorrect in ('MentorFLow', 'Mentor Flow', 'MentoFlow'):
            self.assertNotIn(incorrect, UI)


if __name__ == '__main__':
    unittest.main(verbosity=2)
