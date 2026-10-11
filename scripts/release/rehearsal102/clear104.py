"""Delete only this preview's disposable Locker entries and private test keys."""
from common import APP_ID, TASK, environment
environment()
assert APP_ID == 'MentorFlow.UAT.inapp104'
import sys
from common import ROOT
sys.path.insert(0,str(ROOT))
from refactor.learning.connections import credentials
from refactor.desktop import credential_store
from refactor.desktop.account_identity import AccountIdentity
identity = AccountIdentity('https://localhost:18764/test-realm','test-update-user','更新排练账号')
credentials.SERVICE_NAME = APP_ID+'.Learning.Models.v1'
credentials.AccountCredentials(identity.account_id).clear()
credential_store.SERVICE_NAME = APP_ID+'.Provider.v1'
for purpose in ('oauth-access-token','oauth-refresh-token'):
    credential_store.delete_account_secret(issuer_hash=identity.issuer_hash,
        subject_hash=identity.subject_hash,purpose=purpose)
credential_store.delete_secret('mfcred_update_login_once_v1')
for name in ('test-private.bin','local-api-key.pem'):
    path = TASK/name
    if path.exists(): path.unlink()
print('Cleared only the local test account in preview services; removed test signing/TLS private keys')
