import json

import pytest

from app.product_sync_contract import project,record_id,validate,ProductSyncRejected


def session_row():
    return dict(id='session',workspace_id='workspace',plan_id='plan',
        state=json.dumps({'provider_session_id':'synthetic-handle',
                          'nested':{'access_token':'synthetic-token','local_path':'X:/synthetic'},
                          'completed_tasks':['task']}),
        started_at='2026-09-16',updated_at='2026-09-16',actual_elapsed_minutes=1.0)


def test_only_public_structured_session_state_crosses_device_boundary():
    row=session_row();payload=project('active_teaching_sessions',row)
    state=json.loads(payload['product_record']['values']['state'])
    assert state=={'nested':{},'completed_tasks':['task']}
    assert 'synthetic-handle' not in json.dumps(payload)
    assert 'synthetic-token' not in json.dumps(payload)
    assert 'X:/synthetic' not in json.dumps(payload)


@pytest.mark.parametrize('field,value',[('record_type',[]),('schema','unknown')])
def test_malformed_record_rejected(field,value):
    row=session_row();payload=project('active_teaching_sessions',row)
    payload['product_record'][field]=value
    with pytest.raises(ProductSyncRejected):
        validate('learning_run',record_id('active_teaching_sessions',row),payload)


def test_changed_identity_or_embedded_private_fields_are_not_trusted():
    row=session_row();payload=project('active_teaching_sessions',row)
    with pytest.raises(ProductSyncRejected,match='RECORD_ID'):
        validate('learning_run','different-id',payload)
    payload['product_record']['values']['state']=row['state']
    with pytest.raises(ProductSyncRejected,match='PRIVATE_FIELD'):
        validate('learning_run',record_id('active_teaching_sessions',row),payload)
