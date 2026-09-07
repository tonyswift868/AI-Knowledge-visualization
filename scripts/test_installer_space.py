"""Space-display regression only; no application, installer or UAC launch."""
from pathlib import Path
import unittest

from scripts.prepare_transactional_installer import estimate_install_space_kib


class InstallerSpaceTests(unittest.TestCase):
    def rows(self):
        return {
            'MentorFlowInstallGate.exe': {'size': 4096},
            'SELF-CHECK/package-manifest.json': {'size': 1025},
            '_internal/runtime.dll': {'size': 4097},
        }

    def test_includes_manifest_allocation_controls_and_uninstaller(self):
        # Four allocated 4KiB blocks, three parents, control reserve,
        # helper-bearing uninstaller + 1MiB stub allowance; NSIS uses KiB.
        expected = (4 * 4096 + 7 * 4096 + 64 * 1024**2 + 4096 + 1024**2) // 1024
        self.assertEqual(estimate_install_space_kib(self.rows()), expected)

    def test_large_payload_does_not_overflow_32_bit_byte_arithmetic(self):
        rows = self.rows()
        rows['_internal/large.dll'] = {'size': 5 * 1024**3 + 1}
        self.assertGreater(estimate_install_space_kib(rows), 5 * 1024**2)

    def test_growth_changes_display_estimate(self):
        rows = self.rows()
        before = estimate_install_space_kib(rows)
        rows['_internal/runtime.dll']['size'] += 4096
        self.assertEqual(estimate_install_space_kib(rows), before + 4)

    def test_inventory_requires_nonempty_helper(self):
        for rows in ({}, {'MentorFlow.exe': {'size': 1}},
                     {'MentorFlowInstallGate.exe': {'size': 0}}):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                estimate_install_space_kib(rows)

    def test_invalid_sizes_fail_closed(self):
        for size in (-1, 1.2, '100', True, None, 2**63):
            rows = self.rows()
            rows['_internal/runtime.dll']['size'] = size
            with self.subTest(size=size), self.assertRaises(ValueError):
                estimate_install_space_kib(rows)

    def test_verified_value_reaches_native_section_and_runtime_gate_remains(self):
        root = Path(__file__).resolve().parents[1]
        wrapper = (root / 'scripts/build_windows_installer.ps1').read_text(encoding='utf-8')
        nsis = (root / 'installer/ProtectedInstall.nsh').read_text(encoding='utf-8')
        self.assertIn('$verifiedInventory.estimated_space_kib', wrapper)
        self.assertIn('/DINSTALL_SPACE_KIB=$spaceKib', wrapper)
        section = nsis.split('Section "MentorFlow"', 1)[1].split('SectionEnd', 1)[0]
        self.assertIn('AddSize ${INSTALL_SPACE_KIB}', section)
        self.assertIn('!ifndef INSTALL_SPACE_KIB', nsis)
        self.assertIn('!if ${INSTALL_SPACE_KIB} <= 0', nsis)
        self.assertIn('!if ${INSTALL_SPACE_KIB} > 2147483647', nsis)
        self.assertIn('需要空间：约 ${INSTALL_SPACE_MB} MB', nsis)
        self.assertNotIn('所需新增空间（字节）',nsis)
        self.assertNotIn('MB_OKCANCEL',nsis.split('Function ValidateDirectory',1)[1].split('FunctionEnd',1)[0])
        self.assertIn('preflight $HelperArgs', nsis)
        self.assertNotIn('SpaceTexts none', nsis)
        self.assertNotIn('SetAutoClose true', nsis)


if __name__ == '__main__':
    unittest.main()
