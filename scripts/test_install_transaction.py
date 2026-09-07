"""Real D: file transactions with injected ACL/registry adapters, never real installs.
These prove rollback logic, not elevated NTFS or visible NSIS interaction.
"""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from scripts import windows_install_transaction as tx
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v

class Registry:
    def __init__(self): self.values={}
    def snapshot(self): return dict(self.values)
    def restore(self,values): self.values=dict(values)
    def publish(self,root,version): self.values={'InstallLocation':str(root),'Version':version}

class Shortcut:
    def __init__(self): self.value=None
    def snapshot(self): return self.value
    def restore(self,value): self.value=value
    def publish(self,root,ids): self.value=str(root/'App/MentorFlow.exe')
    def points_to(self,root): return self.value==str(root/'App/MentorFlow.exe')

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name); self.source=self.base/'source'; self.source.mkdir()
        self.root=self.base/'selected'; self.uninstaller=self.base/'Uninstall.exe'; self.uninstaller.write_bytes(b'MZ fixture')
        required=['MentorFlow.exe','SELF-CHECK/MentorFlowSelfCheck.exe','SELF-CHECK/verify_windows_package.py',
            'SELF-CHECK/mentorflow-check.bat','SELF-CHECK/mentorflow-update.bat','_internal/demo.dist-info/INSTALLER']
        for key in required:
            p=self.source/key; p.parent.mkdir(parents=True,exist_ok=True); p.write_bytes(b'fixture')
        (self.source/'mentorflow-version.json').write_text(json.dumps({'schema':v.VERSION_SCHEMA,'product':'MentorFlow','version':'1.1.0'}),encoding='utf-8')
        self.pin=v.sha256_file(v.build_manifest(self.source))
        self.ids=dict(manifest_sha256=self.pin,source_sha='a'*40,tree_sha='b'*40)
        self.registry=Registry(); self.shortcut=Shortcut()
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        def create(path,*,directory):
            if directory: path.mkdir(); return None
            return path.open('xb')
        replacements={
            'assert_protected':lambda p:None,'assert_safe_parents':lambda p:None,
            '_root':lambda p:Path(p),'_set_protected':lambda p:None,'_create_protected':create,
        }
        for name,value in replacements.items(): self.stack.enter_context(patch.object(trust,name,value))
        self.stack.enter_context(patch.object(tx.c.windll.shell32,'IsUserAnAdmin',return_value=True))
        from types import SimpleNamespace
        original_dll=tx.c.WinDLL
        self.stack.enter_context(patch.object(tx.c,'WinDLL',side_effect=lambda name,*a,**kw:
            SimpleNamespace(IsUserAnAdmin=lambda:True) if name=='shell32' else original_dll(name,*a,**kw)))
        self.stack.enter_context(patch.object(tx,'cleanup_failed',return_value=0))
    def transaction(self,**kwargs):
        return tx.Transaction(self.source,self.root,self.ids,self.uninstaller,registry=self.registry,shortcut=self.shortcut,**kwargs)
    def assert_fresh_rolled_back(self):
        self.assertFalse((self.root/'App').exists()); self.assertFalse((self.root/trust.RECEIPT).exists())
        self.assertEqual(self.registry.values,{}); self.assertIsNone(self.shortcut.value)
        self.assertFalse(self.root.exists())
    def test_success_selected_path_receipt_registration_shortcut(self):
        self.transaction().run()
        self.assertEqual(self.registry.values['InstallLocation'],str(self.root))
        self.assertEqual(self.shortcut.value,str(self.root/'App/MentorFlow.exe'))
        self.assertTrue((self.root/'Uninstall.exe').is_file())
        self.assertTrue((self.root/'App/_internal/demo.dist-info/INSTALLER').is_file())
        self.assertEqual(trust.verify_installation(self.root,**self.ids)['verification'],'PASS')
        self.assertFalse((self.root/tx.STAGING).exists())
    def test_cancel_early_mid_and_late(self):
        for target in ('copy_begin','copy_chunk','before_verify','verify','before_commit','after_swap'):
            with self.subTest(target=target):
                def fail(point):
                    if point==target: raise tx.Cancelled(target)
                with self.assertRaises(tx.Cancelled): self.transaction(fault=fail).run()
                self.assert_fresh_rolled_back()
    def test_fresh_fault_matrix(self):
        for target in ('before_verify','before_commit','after_swap','before_metadata','registry','shortcut'):
            with self.subTest(target=target):
                def fail(point):
                    if point==target: raise ValueError(target)
                with self.assertRaises(ValueError): self.transaction(fault=fail).run()
                self.assert_fresh_rolled_back()
    def test_old_install_survives_every_failed_update(self):
        self.transaction().run()
        old_receipt=(self.root/trust.RECEIPT).read_bytes(); old_app=(self.root/'App/MentorFlow.exe').read_bytes()
        old_registry=self.registry.snapshot(); old_shortcut=self.shortcut.snapshot()
        for target in ('copy_chunk','before_verify','after_swap','registry','shortcut'):
            with self.subTest(target=target):
                def fail(point):
                    if point==target: raise ValueError(target)
                with self.assertRaises(ValueError): self.transaction(fault=fail).run()
                self.assertEqual((self.root/trust.RECEIPT).read_bytes(),old_receipt)
                self.assertEqual((self.root/'App/MentorFlow.exe').read_bytes(),old_app)
                self.assertEqual(self.registry.snapshot(),old_registry); self.assertEqual(self.shortcut.snapshot(),old_shortcut)
                self.assertEqual(trust.verify_installation(self.root,**self.ids)['verification'],'PASS')
    def test_hash_failure_during_copy_cleans_staging(self):
        (self.source/'MentorFlow.exe').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'hash mismatch'): self.transaction().run()
        self.assert_fresh_rolled_back()
    def test_manifest_mismatch_no_extraction(self):
        self.ids['manifest_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'manifest identity'): self.transaction().run()
        self.assert_fresh_rolled_back()
    def test_disk_space_before_extraction(self):
        with patch.object(tx.shutil,'disk_usage',return_value=type('Space',(),{'free':0})()):
            with self.assertRaisesRegex(ValueError,'磁盘空间不足'): self.transaction().run()
        self.assert_fresh_rolled_back()
    def test_unrelated_nonempty_target_preserved(self):
        self.root.mkdir(); unknown=self.root/'user-note.txt'; unknown.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError,'未识别'): self.transaction().run()
        self.assertEqual(unknown.read_bytes(),b'keep')
        self.assertEqual(list(self.root.iterdir()),[unknown])
    def test_drive_and_data_roots_rejected(self):
        for path in ('D:/','E:/','E:/MentorFlowData','C:/Windows'):
            with self.subTest(path=path),self.assertRaises(ValueError): tx.validate_target(Path(path))
    def test_uninstaller_control_tamper_fails(self):
        self.transaction().run(); (self.root/'Uninstall.exe').write_bytes(b'tampered')
        with self.assertRaisesRegex(trust.RepairRequired,'control hash'): trust.verify_installation(self.root,**self.ids)
    def test_acl_failure_rolls_back(self):
        original=trust.verify_installation
        with patch.object(trust,'verify_installation',side_effect=trust.RepairRequired('ACL denied')):
            with self.assertRaises(trust.RepairRequired): self.transaction().run()
        self.assert_fresh_rolled_back()
    def test_regression_old_recursive_exclusion_exact_missing_check(self):
        self.transaction().run()
        (self.root/'App/_internal/demo.dist-info/INSTALLER').unlink()
        with self.assertRaisesRegex(trust.RepairRequired,'file set changed'):
            trust.verify_installation(self.root,**self.ids,activate=True,
                control_hashes={'Uninstall.exe':tx.sha(self.root/'Uninstall.exe')})

    def test_real_shortcut_com_roundtrip_without_launch(self):
        from scripts.windows_install_shortcut import _save_link,read_link
        path=self.base/'sample.lnk'; target=self.source/'MentorFlow.exe'
        _save_link(path,target,'--fixture',self.source,target)
        self.assertEqual(read_link(path),dict(target=str(target),arguments='--fixture',
            workdir=str(self.source),icon=str(target)))

    def test_mutex_refuses_concurrent_transaction(self):
        with tx.installer_lock():
            with self.assertRaisesRegex(ValueError,'另一个'):
                with tx.installer_lock(): self.fail('concurrent installer admitted')

    def test_uninstall_removes_only_committed_program(self):
        class NestedRegistry(Registry):
            def publish(self,root,version):
                self.values={tx.REG_PRODUCT:{'InstallLocation':[str(root),1],'Version':[version,1]}}
        self.registry=NestedRegistry()
        self.transaction().run()
        tx.uninstall(self.root,self.registry,self.shortcut)
        self.assertFalse(self.root.exists()); self.assertIsNone(self.shortcut.snapshot())
        self.assertTrue(all(v is None for v in self.registry.values[tx.REG_PRODUCT].values()))

    def test_setup_failure_after_journal_rolls_back(self):
        original=trust._create_protected
        def fail(path,*,directory):
            if path.name=='prior-program': raise OSError('injected allocation failure')
            return original(path,directory=directory)
        with patch.object(trust,'_create_protected',side_effect=fail):
            with self.assertRaises(OSError): self.transaction().run()
        self.assert_fresh_rolled_back()

    def test_previous_location_recognition_and_unknown_refusal(self):
        self.assertFalse(tx.recognised(self.root))
        self.transaction().run(); self.assertTrue(tx.recognised(self.root))
        (self.root/'App'/v.MANIFEST_RELATIVE).write_bytes(b'changed')
        self.assertFalse(tx.recognised(self.root))

    def test_interrupted_rollback_is_recoverable_without_deleting_old_app(self):
        self.transaction().run()
        old=(self.root/trust.RECEIPT).read_bytes()
        original=Path.rename; interrupted=[False]
        def rename(path,target):
            if path.parent.name=='prior-program' and path.name==trust.RECEIPT and not interrupted[0]:
                interrupted[0]=True; raise OSError('rollback interrupted')
            return original(path,target)
        def fail(point):
            if point=='registry': raise ValueError('registry fault')
        with patch.object(Path,'rename',rename):
            with self.assertRaises(OSError): self.transaction(fault=fail).run()
        self.transaction().recover()
        self.assertEqual((self.root/trust.RECEIPT).read_bytes(),old)
        self.assertEqual(trust.verify_installation(self.root,**self.ids)['verification'],'PASS')

    def test_first_staging_allocation_failure_removes_created_root(self):
        original=trust._create_protected
        def fail(path,*,directory):
            if path.name==tx.STAGING: raise OSError('allocation fault')
            return original(path,directory=directory)
        with patch.object(trust,'_create_protected',fail):
            with self.assertRaises(OSError): self.transaction().run()
        self.assert_fresh_rolled_back()

if __name__=='__main__': unittest.main()
