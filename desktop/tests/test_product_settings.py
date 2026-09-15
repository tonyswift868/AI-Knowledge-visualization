from desktop.product_settings import AccountProductSettings
from desktop.api import DesktopApi, ApiError
import httpx
import pytest


class Local:
    def __init__(self): self.data={}
    def value(self,key,default=None):return self.data.get(key,default)
    def setValue(self,key,value):self.data[key]=value
    def remove(self,key):self.data.pop(key,None)


def test_normal_settings_mapping_does_not_export_device_or_credentials():
    from desktop.product_setting_policy import ACCOUNT_SYNC_SETTINGS,DEVICE_LOCAL_SETTINGS
    assert len(ACCOUNT_SYNC_SETTINGS)==10 and len(DEVICE_LOCAL_SETTINGS)==28
    assert not set(ACCOUNT_SYNC_SETTINGS)&set(DEVICE_LOCAL_SETTINGS)
    sent=[]
    class Api:
        def request(self,method,path,**kwargs):
            sent.append((method,path,kwargs))
            return {'analogy/keywords':'Chess','task-colors':{'task':'blue'}} if method=='GET' else {}
    local=Local();settings=AccountProductSettings(local,Api(),lambda message:None)
    key='account-history/v1/issuer/subject/workspace/analogy/keywords'
    assert settings.value(key)=='Chess'
    settings.setValue(key,'Cycling')
    settings.setValue('task-colors/task','green')
    settings.setValue('provider/credential_ref','local-reference')
    settings.setValue('appearance/classroom_theme','dark')
    assert settings.value('task-colors/task')=='green'
    assert local.data=={'provider/credential_ref':'local-reference','appearance/classroom_theme':'dark'}
    assert len(sent)==3
    assert 'local-reference' not in repr(sent)


def test_desktop_api_reuses_command_after_timeout_and_releases_after_ack():
    calls=[]
    def respond(request):
        calls.append(request.headers['X-MentorFlow-Command-ID'])
        return httpx.Response(503 if len(calls)==1 else 200,json={'error':{'code':'PRODUCT_SYNC_PENDING'}} if len(calls)==1 else {'ok':True})
    api=DesktopApi('http://127.0.0.1:12345')
    api.client.close();api.client=httpx.Client(base_url=api.origin,transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ApiError):api.request('PUT','/api/product-settings/theme',json={'value':'dark'})
        api.request('PUT','/api/product-settings/theme',json={'value':'dark'})
        api.request('PUT','/api/product-settings/theme',json={'value':'dark'})
        assert calls[0]==calls[1] and calls[2]!=calls[1]
    finally:api.close()


@pytest.mark.parametrize('mode',['import_managed','import_linked'])
def test_vault_import_retry_preserves_original_asset_after_lost_ack(tmp_path,mode):
    from desktop.account_identity import AccountProfile
    from desktop.account_vault import AccountVaultManager,VaultLayout
    from desktop.product_asset_metadata import bind_vault_metadata
    manager=AccountVaultManager(VaultLayout(tmp_path/'vault'))
    vault=manager.mount(AccountProfile.personal_local())
    calls=[]
    class Api:
        def request(self,method,path,**kwargs):
            calls.append((path,kwargs))
            if len(calls)==1:raise TimeoutError('synthetic metadata ACK loss')
            return {}
    source=tmp_path/'test.txt';source.write_bytes(b'owned synthetic test file')
    try:
        bind_vault_metadata(vault,Api())
        with pytest.raises(TimeoutError):getattr(vault,mode)(source)
        original=vault.list_assets()[0].local_asset_id
        restored=getattr(vault,mode)(source)
        assert restored.local_asset_id==original
        assert len(vault.list_assets())==1
        assert calls[0]==calls[1]
        assert vault._database.execute('SELECT COUNT(*) FROM asset_metadata_pending').fetchone()[0]==0
    finally:manager.unmount()


def test_model_missing_view_uses_model_label(qtbot):
    from desktop.local_asset_page import LocalAssetPage,AccountResourceRow
    class Lifecycle:
        def relink(self,*args):pass
    page=LocalAssetPage(Lifecycle(),list_resources=lambda:[AccountResourceRow(
        'model-X','model-X.gguf',22,'MODEL','MISSING_ON_THIS_DEVICE',('relink',))])
    qtbot.addWidget(page)
    assert page.table.item(0,1).text()=='模型 · 本机安装'
    assert '缺少原文件' in page.table.item(0,2).text()
