import json

import pytest

from refactor.server.product_sync_contract import project,record_id,validate,ProductSyncRejected


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


@pytest.mark.parametrize('table', ['active_plan_calendars', 'active_replan_history', 'orm_plan_activation_records'])
def test_measured_aggregate_capacity_preserves_validation(table):
    from refactor.server.product_sync_contract import RECORDS
    spec=RECORDS[table]
    row={column: None for column in spec['columns']}
    row.update({key:'synthetic-large' for key in spec['keys']})
    field=next(column for column in spec['columns'] if column not in spec['keys'])
    row[field]='x'*6_458_439
    payload=project(table,row)
    validate(spec['kind'],record_id(table,row),payload)
    row[field]='x'*(8*1024*1024)
    with pytest.raises(ProductSyncRejected,match='TOO_LARGE'): project(table,row)


def test_other_records_keep_256k_limit():
    row=session_row();row['state']='x'*262144
    with pytest.raises(ProductSyncRejected,match='TOO_LARGE'): project('active_teaching_sessions',row)


def test_large_untyped_payload_cannot_borrow_aggregate_allowance():
    with pytest.raises(ProductSyncRejected,match='TOO_LARGE'):
        validate('setting','untyped',{'value':'x'*262144})
