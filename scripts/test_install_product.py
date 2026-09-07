import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from scripts.install_product import Product, configured
from scripts.windows_install_transaction import Registry


def test_uat_has_disjoint_namespace():
    production=Product();uat=Product('MentorFlow.UAT.MF14')
    assert set(production.registry_fields).isdisjoint(uat.registry_fields)
    assert production.shortcut_name != uat.shortcut_name
    for root in ('E:/MentorFlow','E:/MentorFlowData','E:/MentorFlowData/child','D:/MFUAC','D:/MFUAC/App','E:/'):
        with pytest.raises(ValueError):uat.validate_root(root)
    uat.validate_root('D:/MF14I')


def test_config_is_build_module_not_environment(monkeypatch):
    monkeypatch.setenv('MENTORFLOW_APP_ID','MentorFlow.UAT.attacker')
    assert configured()==Product()
    with patch.dict(sys.modules,mentorflow_delivery_authority=SimpleNamespace(APP_ID='MentorFlow.UAT.MF14')):
        assert configured()==Product('MentorFlow.UAT.MF14')


def test_uat_registry_restore_rejects_formal_before_open():
    registry=Registry(Product('MentorFlow.UAT.MF14'))
    with patch('winreg.CreateKeyEx') as create:
        with pytest.raises(ValueError,match='namespace'):
            registry.restore({Product().product_key:{'InstallLocation':['E:/MentorFlow',1]}})
        create.assert_not_called()
