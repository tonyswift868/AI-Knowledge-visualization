import ast
from pathlib import Path
import sys
import unittest
from pydantic import ValidationError

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from app.enterprise_tenancy.structured_sync import Plan, Progress, Resource, Write
from desktop.structured_sync import StructuredSyncClient

class ContractTests(unittest.TestCase):
    def test_authority_and_binary_fields_forbidden(self):
        for extra in ('issuer','subject_id','workspace_id','local_path','password','raw_bytes'):
            with self.assertRaises(ValidationError):
                Resource.model_validate({'filename':'a.pdf','media_type':'application/pdf','sha256':'a'*64,'size_bytes':1,extra:'not-admitted'})

    def test_strict_revisions_and_nonfinite_progress(self):
        for revision in (True,-1,'1'):
            with self.assertRaises(ValidationError):Write(request_id='x',expected_revision=revision,payload={})
        for mastery in (float('nan'),float('inf'),-1,1.01):
            with self.assertRaises(ValidationError):Progress(plan_id='p',mastery=mastery,completed_tasks=0)

    def test_invalid_client_route_never_calls_transport(self):
        class NoNetwork:
            def put(self,*a,**k):raise AssertionError('Transport must not be called')
        client=StructuredSyncClient(NoNetwork())
        for value in ('..','../other','a/b','a?b',''):
            with self.assertRaises(ValueError):client.write('plan',value,request_id='x',expected_revision=0,payload={})

    def test_core_runtime_wires_same_router_and_transaction_service(self):
        tree=ast.parse((ROOT/'backend/app/account_core_mvp.py').read_text(encoding='utf-8'))
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call)]
        self.assertTrue(any(isinstance(n.func,ast.Attribute) and n.func.attr=='include_router' and
            n.args and isinstance(n.args[0],ast.Name) and n.args[0].id=='structured_sync_router' for n in calls))
        self.assertTrue(any(isinstance(n.func,ast.Name) and n.func.id=='StructuredSyncService' for n in calls))

if __name__=='__main__':unittest.main(verbosity=2)
