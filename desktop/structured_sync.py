"""Small server-authoritative rehydration client; receives authenticated transport.

No identity mapping, credential storage or local data-root selection lives here.
The resource availability resolver is device-local and must verify its own bytes.
"""
import re

class StructuredSyncClient:
    PREFIX = '/api/enterprise/sync/v1'

    def __init__(self, authenticated_transport, *, resource_is_present=None):
        self.transport = authenticated_transport
        self.resource_is_present = resource_is_present or (lambda entity: False)

    def _path(self, kind, entity_id):
        if kind not in ('plan', 'progress', 'resource') or not re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}', entity_id):
            raise ValueError('Invalid structured sync entity ID or kind')
        return f'{self.PREFIX}/entities/{kind}/{entity_id}'

    def write(self, kind, entity_id, *, request_id, expected_revision, payload):
        response = self.transport.put(self._path(kind, entity_id), json={
            'request_id':request_id, 'expected_revision':expected_revision, 'payload':payload})
        response.raise_for_status()
        return response.json()

    def delete(self, kind, entity_id, *, request_id, expected_revision):
        response = self.transport.post(self._path(kind, entity_id)+'/delete', json={
            'request_id':request_id, 'expected_revision':expected_revision})
        response.raise_for_status()
        return response.json()

    def rehydrate(self):
        response = self.transport.get(f'{self.PREFIX}/snapshot')
        response.raise_for_status()
        state = response.json()
        if state.get('schema') != 'mentorflow.structured-sync.v1' or state.get('complete') is not True:
            raise ValueError('Incomplete or unsupported server snapshot')
        if state.get('contract_version') not in (2, 3) or not re.fullmatch(
                r'pg-snapshot:\d+:\d+:(?:\d+(?:,\d+)*)?', state.get('snapshot_revision', '')):
            raise ValueError('Authoritative snapshot observation required')
        result = {'plans':[], 'progress':[], 'resources':[], 'tombstones':[]}
        resource_projection = {r['resource_id']: r for r in state['resources']}
        for entity in state['entities']:
            if entity['deleted']:
                result['tombstones'].append(entity)
                continue
            if entity['kind'] == 'resource':
                projection = resource_projection[entity['entity_id']]
                entity = dict(entity, product_classification=projection['product_classification'],
                              plan_ids=projection['plan_ids'], device_availability='AVAILABLE_ON_THIS_DEVICE' if
                              self.resource_is_present(entity) else 'MISSING_ON_THIS_DEVICE')
            result[{'plan':'plans','progress':'progress','resource':'resources'}[entity['kind']]].append(entity)
        # Rebuild from the authoritative snapshot, never merge omitted cached objects.
        result['snapshot_revision'] = state['snapshot_revision']
        result['snapshot_scope'] = state['snapshot_scope']
        result['learning_state'] = state['learning_state']
        result['progress_provenance'] = state['progress_provenance']
        result['observation'] = dict(state['observation'],
            resource_missing_state='DEVICE_LOCAL_RESOLVER',
            missing_resources=[r['entity_id'] for r in result['resources']
                               if r['device_availability'] == 'MISSING_ON_THIS_DEVICE'])
        return result
