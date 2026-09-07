import copy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch
import tempfile

from scripts import install_receipt_state as state
from scripts import windows_install_trust as trust


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='MF13-state-',dir='E:/Codex/Temp')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'App').mkdir();(self.root/'.uat-control').mkdir()
        self.bootstrap=json.loads(Path('D:/MFUAC/.uat-control/acl-bootstrap-result.json').read_bytes())
        self.bootstrap['UAT_ROOT']=str(self.root)
        (self.root/'.uat-control/acl-bootstrap-result.json').write_text(json.dumps(self.bootstrap))
        (self.root/'App/payload-sentinel.txt').write_bytes(self.bootstrap['SENTINEL_TEXT'].encode())
        self.addCleanup(patch.stopall)
        patch.object(trust,'assert_protected').start()
        patch.object(trust,'_root',side_effect=lambda p:p).start()

    def receipt(self):
        return dict(schema_version=1,record_type=state.INSTALLED_KIND,state='HEALTHY',immutable_tree=True,
            binding=dict(schema=trust.SCHEMA,source_sha='1'*40,tree_sha='2'*40,manifest_sha256='3'*64,version='1.1.0',root=str(self.root),root_identity=[self.root.stat().st_dev,self.root.stat().st_ino]),
            inventory={},file_set_identity='4'*64,critical_hashes={},full_verified=True,full_verified_at=time.time())

    def test_missing_receipt_preseed(self):
        result=state.inspect_root(self.root)
        self.assertEqual(result['state'],'ACL_READY_NO_BASE')
        self.assertTrue(result['can_seed_base'])

    def test_other_json_never_receipt(self):
        for namespace,kind in [('.uat-control',state.BOOTSTRAP_KIND),('health',state.HEALTH_KIND),('transaction',state.TRANSACTION_KIND),('random',state.INSTALLED_KIND)]:
            with self.subTest(namespace=namespace):
                directory=self.root/namespace;directory.mkdir(exist_ok=True)
                (directory/'looks-like-receipt.json').write_text(json.dumps(self.receipt()|{'record_type':kind}))
                self.assertEqual(state.inspect_root(self.root)['state'],'ACL_READY_NO_BASE')
        self.assertFalse((self.root/trust.RECEIPT).exists())

    def test_app_without_receipt_rejects(self):
        (self.root/'App/MentorFlow.exe').write_bytes(b'not-installed')
        with self.assertRaisesRegex(state.ReceiptStateError,'INCONSISTENT'):state.inspect_root(self.root)

    def test_malformed_canonical_rejects(self):
        (self.root/trust.RECEIPT).write_text('{')
        with self.assertRaisesRegex(state.ReceiptStateError,'malformed'):state.inspect_root(self.root)

    def test_bootstrap_at_canonical_not_receipt(self):
        (self.root/trust.RECEIPT).write_text(json.dumps(self.bootstrap))
        with self.assertRaises(state.ReceiptStateError):state.inspect_root(self.root)

    def test_wrong_record_types_reject(self):
        for kind in (state.BOOTSTRAP_KIND,state.HEALTH_KIND,state.TRANSACTION_KIND,state.RELEASE_KIND):
            with self.subTest(kind=kind):
                (self.root/trust.RECEIPT).write_text(json.dumps(self.receipt()|{'record_type':kind}))
                with self.assertRaisesRegex(state.ReceiptStateError,'record type'):state.inspect_root(self.root)

    def test_valid_receipt_delegates_full_integrity(self):
        (self.root/trust.RECEIPT).write_text(json.dumps(self.receipt()))
        with patch.object(trust,'verify_installation',return_value={'verification':'PASS'}) as full:
            self.assertEqual(state.inspect_root(self.root)['state'],'BASE_INSTALLED_HEALTHY')
            full.assert_called_once()
        with patch.object(trust,'verify_installation',side_effect=trust.RepairRequired('tampered')):
            with self.assertRaises(trust.RepairRequired):state.inspect_root(self.root)

    def test_exact_legacy_schema_only(self):
        doc=self.receipt()
        for field in ('schema_version','record_type','state','immutable_tree'):doc.pop(field)
        state.validate_installed_record(doc,self.root)
        doc['binding']['schema']='looks-like-installed'
        with self.assertRaises(state.ReceiptStateError):state.validate_installed_record(doc,self.root)

    def test_binding_negatives(self):
        for key,value in [('root','D:\\Other'),('manifest_sha256','bad'),('version','bad'),('root_identity',[True,1]),('schema','wrong')]:
            doc=self.receipt();doc['binding'][key]=value
            with self.subTest(key=key),self.assertRaises(state.ReceiptStateError):state.validate_installed_record(doc,self.root)

    def test_sentinel_tamper_reject(self):
        (self.root/'App/payload-sentinel.txt').write_bytes(b'tamper')
        with self.assertRaises(state.ReceiptStateError):state.inspect_root(self.root)

    def test_state_transitions_typed_and_not_guessed(self):
        for source,targets in state.EDGES.items():
            for target in targets:
                record=dict(schema_version=1,record_type=state.TRANSACTION_KIND,from_state=source,to_state=target,attempt_id='a'*32,nonce='b'*64)
                self.assertEqual(state.transition(source,record),target)
                record['record_type']=state.HEALTH_KIND
                with self.assertRaises(state.ReceiptStateError):state.transition(source,record)
        with self.assertRaises(state.ReceiptStateError):state.transition('ACL_READY_NO_BASE',{})

    def test_duplicate_json_reject(self):
        (self.root/trust.RECEIPT).write_text('{"record_type":"x","record_type":"y"}')
        with self.assertRaises(state.ReceiptStateError):state.inspect_root(self.root)

    def test_full_verifier_publishes_typed_receipt_after_all_hashes(self):
        from scripts import verify_windows_package as v
        import ctypes
        from types import SimpleNamespace
        app=self.root/'App'
        (app/'payload-sentinel.txt').unlink()
        for key in ('MentorFlow.exe','SELF-CHECK/MentorFlowSelfCheck.exe','SELF-CHECK/verify_windows_package.py','SELF-CHECK/mentorflow-check.bat','SELF-CHECK/mentorflow-update.bat'):
            path=app/key;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'harmless fixture')
        (app/'mentorflow-version.json').write_text(json.dumps(dict(schema='mentorflow.version.v1',product='MentorFlow',version='1.1.0')))
        pin=v.sha256_file(v.build_manifest(app))
        ids=dict(manifest_sha256=pin,source_sha='1'*40,tree_sha='2'*40)
        original=ctypes.WinDLL
        def dll(name,*args,**kwargs):
            return SimpleNamespace(IsUserAnAdmin=lambda:True) if name=='shell32' else original(name,*args,**kwargs)
        def create(path,*,directory):
            return path.mkdir() if directory else path.open('xb')
        with patch.object(ctypes,'WinDLL',side_effect=dll),patch.object(trust,'_create_protected',side_effect=create):
            # Match the existing install/seed whole-snapshot retry for delayed
            # NTFS copy notifications; never suppress a watcher event.
            for attempt in range(3):
                try:
                    trust.verify_installation(self.root,activate=True,**ids)
                    break
                except v.PackageError as error:
                    if str(error)!='package changed during security verification' or attempt==2:raise
                    time.sleep(.1)
            receipt=state.read_document(self.root/trust.RECEIPT)
            self.assertEqual(receipt['record_type'],state.INSTALLED_KIND)
            self.assertEqual(trust.verify_installation(self.root,**ids)['verification'],'PASS')
            self.assertTrue((self.root/'.uat-control/acl-bootstrap-result.json').exists())
            self.assertNotIn('.uat-control',receipt['inventory'])
            (app/'MentorFlow.exe').write_bytes(b'tamper')
            with self.assertRaises(trust.RepairRequired):trust.verify_installation(self.root,**ids)


if __name__=='__main__':unittest.main()
