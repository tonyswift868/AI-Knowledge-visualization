"""Real widget/source tests, offscreen only; not packaged window qualification."""
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QDialog,QLabel
from desktop.login_shell import LoginGateWindow
from desktop import gui_update_runtime,startup_connection
from scripts.install_product import Product


def test_uat_never_contacts_account_service():
    connection=startup_connection.ConnectionPreparation({'SystemRoot':os.environ['SystemRoot']},Path('D:/MF14W/test-ca.crt'))
    with patch('scripts.install_product.configured',return_value=Product('MentorFlow.UAT.MF14')),\
         patch.object(connection,'endpoint_ready',side_effect=AssertionError('no account HTTP')),\
         patch.object(startup_connection.subprocess,'Popen',side_effect=AssertionError('no SSH')):
        assert connection.connect() is False


def test_real_prelogin_about_no_product_or_false_auth():
    app=QApplication.instance() or QApplication([])
    gate=LoginGateWindow();controller=SimpleNamespace(results={'SECURITY_GATE':True})
    with patch('scripts.install_product.configured',return_value=Product('MentorFlow.UAT.MF14')),\
         patch.object(gui_update_runtime,'notify_health') as notify,\
         patch('desktop.update_panel.configured_mature_service',return_value=None):
        gui_update_runtime.attach_updates(app,gate,controller,Path('D:/MF14W/source-fixture'))
        gate.show();deadline=time.monotonic()+2
        while not gate.about_button.isEnabled() and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert gate.about_button.isEnabled()
        gate.about_button.click();app.processEvents()
        about=gate.findChild(QDialog,'mentorflowRealAboutUpdates')
        assert about is not None and about.isVisible()
        assert any('账号服务未连接' in label.text() for label in about.findChildren(QLabel))
        about.close();gate.close();app.processEvents()


def test_uat_environment_never_reads_formal_profile(tmp_path):
    authority=SimpleNamespace(APP_ID='MentorFlow.UAT.MF14',WORK_ROOT=str(tmp_path),DATA_ROOT=str(tmp_path/'data'))
    with patch.dict(sys.modules,mentorflow_delivery_authority=authority),\
         patch('scripts.windows_install_trust.assert_safe_parents'),patch('scripts.windows_install_trust.assert_protected'),\
         patch('scripts.verify_windows_package.sha256_file',return_value=startup_connection.CA_SHA),\
         patch.object(startup_connection,'read_profile',side_effect=AssertionError('formal profile forbidden')):
        env,_=startup_connection.prepare_environment(tmp_path/'installed')
    assert Path(env['MENTORFLOW_DB']).is_relative_to(tmp_path/'data')
    assert Path(env['TEMP']).is_relative_to(tmp_path/'data')
