"""Run the existing PostgreSQL protocol qualification on the owned native lab."""
import importlib.util
import io
from pathlib import Path
import unittest
import sys

from alembic import command
from sqlalchemy import create_engine
from test_account_import_ledger_schema import native


def test_existing_22_postgres_protocol_cases(native,monkeypatch):
    command.upgrade(native.config, 'head')
    root=Path(__file__).resolve().parents[2]/'tests'/'server_sync'
    classes=[]
    for filename, name in [('test_vertical_slice.py','SyncTests'),
                           ('test_producer_contract.py','ProducerTests'),
                           ('test_empty_authority.py','EmptyAuthorityTests')]:
        spec=importlib.util.spec_from_file_location('product_regression_'+name,root/filename)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        monkeypatch.setitem(sys.modules,Path(filename).stem,module)
        cls=getattr(module,name)
        def setup(cls):
            cls.admin=create_engine(native.admin.url,hide_parameters=True)
            cls.url=native.app.url.render_as_string(hide_password=False)
        cls.setUpClass=classmethod(setup)
        classes.append(cls)
    result=unittest.TextTestRunner(stream=io.StringIO()).run(unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes))
    assert result.testsRun==22
    assert not result.skipped
    assert result.wasSuccessful(), [(str(case), trace.replace(native.app.url.password or '', '[fixture]'))
                                     for case,trace in result.failures+result.errors]
