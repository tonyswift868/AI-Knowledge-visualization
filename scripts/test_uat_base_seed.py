import copy
import json
from pathlib import Path
import time
import unittest
from scripts.uat_base_seed import check_request
from scripts.install_receipt_state import ReceiptStateError


class SeedRequestTests(unittest.TestCase):
    def setUp(self):
        self.authority=json.loads(Path('E:/Codex/Temp/MF13-build/authority.json').read_bytes())
        self.request=dict(self.authority,schema_version=1,record_type='mentorflow-uat-base-seed-request',
            attempt_id='a'*32,nonce='b'*64,created_at=100,expires_at=200)

    def test_valid(self):check_request(self.request,self.authority,now=150)

    def test_binding_and_schema_negatives(self):
        bad=[('install_root','E:\\MentorFlow'),('source_root','E:\\Other'),('root_identity',[0,0]),
             ('bootstrap_sha256','0'*64),('manifest_sha256','0'*64),('app_exe_sha256','0'*64),
             ('source_sha','0'*40),('tree_sha','0'*40),('version','9.0.0'),
             ('schema_version',2),('record_type','mentorflow-installed-receipt'),
             ('attempt_id','../escape'),('nonce','wrong'),('expires_at',149),('created_at',151),
             ('expires_at',1001),('command','whoami')]
        for key,value in bad:
            with self.subTest(key=key):
                doc=dict(self.request);doc[key]=value
                with self.assertRaises(ReceiptStateError):check_request(doc,self.authority,now=150)


if __name__=='__main__':unittest.main()
