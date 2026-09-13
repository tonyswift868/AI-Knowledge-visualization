"""Bounded producer-to-presentation projection, published through the session fence."""
import json
import os
from pathlib import Path
import uuid


def project(state, context):
    scope, observation, learning = state['snapshot_scope'], state['observation'], state['learning_state']
    if (scope['subject_id'] != context.subject or scope['organization_id'] != context.organization_id
            or scope['workspace_id'] != context.workspace_id or observation['source'] != 'SERVER_CONFIRMED'
            or observation['snapshot_revision'] != state['snapshot_revision']):
        raise ValueError('REHYDRATION_SCOPE_MISMATCH')
    empty = learning['status']
    if empty not in {'NONEMPTY', 'TRUE_EMPTY_SUPPORTED_SCOPE', 'PARTIAL_UNSUPPORTED', 'NOT_INITIALIZED'}:
        raise ValueError('REHYDRATION_AUTHORITY_REQUIRED')
    progress = {}
    for item in state['progress']:
        value = item['payload']
        progress.setdefault(value['plan_id'], []).append(
            f"掌握度 {value['mastery']:.0%} · 已完成 {value['completed_tasks']} 项")
    plans = [dict(key=p['entity_id'], name=p['payload']['title'],
                  progress='；'.join(progress.get(p['entity_id'], ['尚无进度记录']))) for p in state['plans']]
    resources = [dict(key=r['entity_id'], plan_key='', plan_keys=r['plan_ids'],
        name=r['payload']['filename'], kind={'USER_FILE':'file', 'MODEL':'model', 'OTHER':'other', 'UNKNOWN':'unknown'}[
            r['product_classification']], missing=r['device_availability'] == 'MISSING_ON_THIS_DEVICE',
        verified_redownload=False, package_supported=False) for r in state['resources']]
    return dict(schema='mentorflow.rehydration-ui.v1', account_key=context.account_id,
        revision=state['snapshot_revision'], plans=plans, resources=resources,
        progress_count=len(state['progress']), review_count=None, history_count=None,
        empty_account=False, empty_state=empty, supported_scope=learning['supported_scope'],
        server_confirmed=True, trusted_cache=False, last_sync=observation['server_confirmed_at'])


def record_observation(state, context):
    """Only real session publication writes the optional owned test-device observation."""
    root = os.environ.get('MENTORFLOW_TEST_DEVICE_ROOT')
    value = dict(state['observation'], test_id=os.environ.get('MENTORFLOW_TEST_TARGET_ID', 'PRODUCT_SYNC'),
        account=context.account_id, server_workspace=context.workspace_id, workspace=context.workspace_id,
        snapshot_revision=state['snapshot_revision'], last_sync_revision=state['snapshot_revision'],
        cloud_entities_restored_count=sum(len(state[k]) for k in ('plans','progress','resources')),
        missing_local_resources_count=len(state['observation']['missing_resources']))
    if not root:
        return value
    from .test_device_profile import read_profile, plain
    profile = read_profile(root)
    if profile['generation'] != os.environ.get('MENTORFLOW_TEST_DEVICE_GENERATION'):
        raise ValueError('TEST_DEVICE_GENERATION_CHANGED')
    value.update(test_id=os.environ['MENTORFLOW_TEST_TARGET_ID'],
        scenario=os.environ['MENTORFLOW_TEST_SCENARIO'], generation=profile['generation'],
        artifact_sha256=os.environ['MENTORFLOW_TEST_ARTIFACT_SHA256'],
        account=context.account_id, server_workspace=context.workspace_id,
        workspace=context.workspace_id, snapshot_revision=state['snapshot_revision'],
        last_sync_revision=state['snapshot_revision'],
        cloud_entities_restored_count=sum(len(state[k]) for k in ('plans','progress','resources')),
        missing_local_resources_count=len(state['observation']['missing_resources']))
    path = plain(Path(root)) / 'observation.json'
    if path.exists():
        plain(path)
        if path.stat().st_nlink != 1:
            raise ValueError('TEST_DEVICE_UNSAFE_PATH')
    temporary = path.with_name('observation.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temporary.replace(path)
    return value


def present(page, generation, envelope):
    from .rehydration_transport import RehydrationTransportError
    def consume(state, context):
        from .rehydration_state import CloudView, SyncState
        if generation != page.model.generation or page.model.state != SyncState.SYNCING:
            return False
        payload = project(state, context)
        CloudView.parse(payload)
        observation = record_observation(state, context)
        accepted = page.receive(generation, 'SYNC_COMPLETE', payload)
        if accepted and page.model.snapshot is not None:
            page.runtime_observation = observation
        return accepted
    try:
        return envelope.publish(consume)
    except RehydrationTransportError as exc:
        status = 'NOT_AUTHORIZED' if exc.code in {'UNAUTHENTICATED','SESSION_STALE'} else 'SYNC_FAILED'
        return page.receive(generation, status)
    except (KeyError, ValueError, TypeError, OSError):
        return page.receive(generation, 'SYNC_FAILED')
