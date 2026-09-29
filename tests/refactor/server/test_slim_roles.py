"""Existing product-admin policy on the actual slim HTTP and membership boundary."""
import json
import pytest
from sqlalchemy import text
from refactor.server import app as entry
from refactor.server.account_identity_contract import account_id
from refactor.server.enterprise.identity.application_roles import ApplicationRolePolicy, SUPER_ADMIN_IDS_ENV
from slim_sqlite_support import system, headers, ISSUER


def test_slim_roles_recheck_policy_and_current_membership(system):
    client,_,engine=system
    workspace=client.post('/api/enterprise/account/workspaces/initialize',headers=headers(),json={}).json()['workspaces'][0]
    auth=headers(workspace=workspace)
    me='/api/enterprise/auth/me';check='/api/enterprise/auth/admin/check'
    assert client.get(me,headers=auth).json()['role']=='LEARNER'
    assert client.get(check,headers=auth).status_code==403
    runtime=entry.app.state.enterprise_identity_runtime
    runtime.application_roles=ApplicationRolePolicy.from_env({SUPER_ADMIN_IDS_ENV:json.dumps([account_id(ISSUER,'user-a')])})
    assert client.get(me,headers=auth).json()['role']=='SUPER_ADMIN'
    assert client.get(check,headers=auth).json()=={'authorized':True,'role':'SUPER_ADMIN'}
    runtime.application_roles=ApplicationRolePolicy()
    assert client.get(check,headers=auth).status_code==403
    with engine.begin() as connection:
        connection.execute(text("UPDATE workspace_memberships SET status='revoked'"))
    assert client.get(me,headers=auth).status_code==403


@pytest.mark.parametrize('raw',['{}','["admin"]','[null]','[1]'])
def test_slim_roles_preserve_existing_configuration_validation(raw):
    with pytest.raises(ValueError):
        ApplicationRolePolicy.from_env({SUPER_ADMIN_IDS_ENV:raw})
