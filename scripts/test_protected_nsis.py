"""Bounded installer wiring checks; no elevation, installation or user DB."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import pefile

ROOT = Path(__file__).resolve().parents[1]

class ProtectedNsisTests(unittest.TestCase):
    def test_order_and_no_elevated_product_launch(self):
        source=(ROOT/'installer/ProtectedInstall.nsh').read_text(encoding='utf-8')
        order=['MUI_PAGE_WELCOME','MUI_PAGE_DIRECTORY','MUI_PAGE_INSTFILES','MUI_PAGE_FINISH']
        positions=[source.index(part) for part in order]
        self.assertEqual(positions,sorted(positions))
        self.assertIn('RequestExecutionLevel admin',source)
        self.assertNotIn('MUI_FINISHPAGE_RUN',source)
        self.assertNotIn('RMDir',source)
        self.assertNotIn('powershell.exe',source)
        self.assertIn('InstallDir "E:\\MentorFlow"',source)
        self.assertNotIn('StrCpy $INSTDIR "E:\\MentorFlow"',source)
        self.assertNotIn('File /r',source)
        self.assertIn('ShowInstDetails hide',source)
        self.assertIn('!define MUI_CUSTOMFUNCTION_ABORT RequestCancel',source)
        self.assertIn('Function RequestCancel',source)
        self.assertIn('EnableWindow $0 1',source)
        self.assertIn('--cancel-file "$CancelFile"',source)
        self.assertIn('ReadRegStr $PreviousRoot',source)
        self.assertIn('recognise --install-root "$PreviousRoot"',source)
        self.assertIn('uninstall-transaction --install-root "$INSTDIR"',source)

    def test_helper_is_windowed_as_invoker_and_nonadmin_refuses(self):
        with tempfile.TemporaryDirectory() as folder:
            exe=Path(folder)/'root.exe'
            result=subprocess.run([str(Path(os.environ['SystemRoot'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'),
                '/nologo','/target:winexe','/out:'+str(exe),
                '/win32manifest:'+str(ROOT/'installer/nsis-helper.manifest'),
                str(ROOT/'installer/ProtectedStage.cs'),str(ROOT/'installer/PrepareNsisRoot.cs')],
                capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            pe=pefile.PE(str(exe))
            self.assertEqual(pe.OPTIONAL_HEADER.Subsystem,2)
            manifests=[pe.get_data(lang.data.struct.OffsetToData,lang.data.struct.Size)
                for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries if entry.id==24
                for item in entry.directory.entries for lang in item.directory.entries]
            self.assertTrue(any(b'asInvoker' in value for value in manifests))
            pe.close()
            import ctypes
            if ctypes.windll.shell32.IsUserAnAdmin():
                self.skipTest('Do not create real install root from an elevated test')
            before=Path('E:/MentorFlow').exists()
            attempt=subprocess.run([str(exe)],creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)
            self.assertEqual(attempt.returncode,3)
            self.assertEqual(Path('E:/MentorFlow').exists(),before)

    def test_protected_root_policy_and_existing_root_preservation(self):
        source=(ROOT/'installer/ProtectedStage.cs').read_text(encoding='utf-8')
        part=source.split('internal static void PrepareUatRoot()',1)[1].split('private void CreateDirectory',1)[0]
        self.assertLess(part.index('AssertSafeParents'),part.index('stage.CreateDirectory(root)'))
        self.assertLess(part.index('Directory.Exists(root)'),part.index('stage.CreateDirectory(root)'))
        self.assertIn('O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)',source)

if __name__=='__main__': unittest.main()
