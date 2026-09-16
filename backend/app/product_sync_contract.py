"""Explicit product record projections for the existing structured-sync protocol.

This is an allowlist, not a database exporter. Raw chunks, file bytes, indexes,
provider credentials and local file locators are outside this contract.
"""
from __future__ import annotations
import hashlib
import json
import math

SCHEMA = "mentorflow.product-record.v1"
RECORDS = {'active_materials': {'kind': 'resource',
                      'columns': ('id',
                                  'workspace_id',
                                  'original_filename',
                                  'media_type',
                                  'size_bytes',
                                  'sha256',
                                  'uploaded_at',
                                  'status',
                                  'page_count',
                                  'parser_name',
                                  'archived',
                                  'archived_at',
                                  'archive_reason',
                                  'material_revision',
                                  'source_kind'),
                      'keys': ('id',),
                      'local_defaults': {'stored_path': '',
                                         'provenance_json': '{}',
                                         'parse_error': '',
                                         'source_url': ''}},
 'active_plans': {'kind': 'plan',
                  'columns': ('id',
                              'workspace_id',
                              'receipt',
                              'created_at',
                              'feasible',
                              'total_estimated_task_minutes',
                              'unscheduled_task_minutes',
                              'warning'),
                  'keys': ('id',),
                  'local_defaults': {}},
 'orm_plans': {'kind': 'plan',
               'columns': ('id',
                           'workspace_id',
                           'version',
                           'title',
                           'status',
                           'deadline',
                           'capacity_status',
                           'scheduled_minutes',
                           'unscheduled_minutes',
                           'diff',
                           'stale',
                           'created_at'),
               'keys': ('id',),
               'local_defaults': {}},
 'active_plan_tasks': {'kind': 'task',
                       'columns': ('id',
                                   'plan_id',
                                   'workspace_id',
                                   'day_date',
                                   'original_day_date',
                                   'day_index',
                                   'task_order',
                                   'knowledge_point_id',
                                   'title',
                                   'objective',
                                   'source_evidence',
                                   'acceptance_condition',
                                   'estimated_task_minutes',
                                   'completed_task_minutes',
                                   'completed_at',
                                   'actual_elapsed_minutes',
                                   'status',
                                   'task_type',
                                   'learning_phase',
                                   'point_sequence',
                                   'phase_part_index',
                                   'phase_part_count',
                                   'linked_review_item_id',
                                   'review_cycle',
                                   'origin_task_id',
                                   'not_before_date',
                                   'trigger_reason'),
                       'keys': ('id',),
                       'local_defaults': {}},
 'orm_plan_execution_tasks': {'kind': 'task',
                              'columns': ('id',
                                          'plan_id',
                                          'workspace_id',
                                          'map_revision_id',
                                          'knowledge_point_id',
                                          'day_date',
                                          'original_day_date',
                                          'day_index',
                                          'task_order',
                                          'title',
                                          'objective',
                                          'source_evidence',
                                          'acceptance_condition',
                                          'concept_snapshot',
                                          'estimated_task_minutes',
                                          'completed_task_minutes',
                                          'actual_elapsed_minutes',
                                          'status',
                                          'task_type',
                                          'learning_phase',
                                          'point_sequence',
                                          'phase_part_index',
                                          'phase_part_count',
                                          'linked_review_item_id',
                                          'review_cycle',
                                          'origin_task_id',
                                          'not_before_date',
                                          'trigger_reason',
                                          'created_at',
                                          'updated_at',
                                          'completed_at'),
                              'keys': ('id',),
                              'local_defaults': {}},
 'orm_tasks': {'kind': 'task',
               'columns': ('id',
                           'day_id',
                           'lesson_id',
                           'title',
                           'task_type',
                           'estimated_minutes',
                           'status',
                           'started_at',
                           'completed_at',
                           'actual_minutes',
                           'evidence'),
               'keys': ('id',),
               'local_defaults': {}},
 'orm_days': {'kind': 'calendar',
              'columns': ('id', 'plan_id', 'day', 'planned_minutes'),
              'keys': ('id',),
              'local_defaults': {}},
 'active_knowledge_progress_snapshots': {'kind': 'progress',
                                         'columns': ('workspace_id',
                                                     'material_id',
                                                     'normalized_name',
                                                     'name',
                                                     'mastery',
                                                     'completed_task_count',
                                                     'completed_task_minutes',
                                                     'actual_elapsed_minutes',
                                                     'completed_phases',
                                                     'updated_at'),
                                         'keys': ('workspace_id', 'material_id', 'normalized_name'),
                                         'local_defaults': {}},
 'active_mastery_evidence': {'kind': 'progress',
                             'columns': ('id',
                                         'session_id',
                                         'knowledge_point_id',
                                         'evidence_type',
                                         'score',
                                         'passed',
                                         'evidence',
                                         'citations',
                                         'created_at'),
                             'keys': ('id',),
                             'local_defaults': {}},
 'orm_plan_mastery_events': {'kind': 'progress',
                             'columns': ('id',
                                         'workspace_id',
                                         'plan_id',
                                         'task_id',
                                         'map_revision_id',
                                         'knowledge_point_id',
                                         'evidence_hash',
                                         'event_type',
                                         'score',
                                         'passed',
                                         'payload',
                                         'idempotency_key',
                                         'occurred_at'),
                             'keys': ('id',),
                             'local_defaults': {}},
 'orm_progress': {'kind': 'progress',
                  'columns': ('id',
                              'workspace_id',
                              'lesson_id',
                              'mastery',
                              'attempts',
                              'independent_attempts',
                              'hint_attempts',
                              'next_review'),
                  'keys': ('id',),
                  'local_defaults': {}},
 'orm_mastery_events': {'kind': 'progress',
                        'columns': ('id',
                                    'workspace_id',
                                    'lesson_id',
                                    'score',
                                    'delta',
                                    'used_hint',
                                    'independent',
                                    'created_at'),
                        'keys': ('id',),
                        'local_defaults': {}},
 'active_task_time_events': {'kind': 'progress',
                             'columns': ('id',
                                         'event_id',
                                         'task_id',
                                         'session_id',
                                         'elapsed_minutes',
                                         'activity_kind',
                                         'recorded_at'),
                             'keys': ('id',),
                             'local_defaults': {}},
 'active_review_items': {'kind': 'review',
                         'columns': ('id',
                                     'session_id',
                                     'workspace_id',
                                     'plan_id',
                                     'knowledge_point_id',
                                     'source_task_id',
                                     'source_mastery_evidence_id',
                                     'due_date',
                                     'interval_step',
                                     'interval_days',
                                     'review_total_minutes',
                                     'estimated_task_minutes',
                                     'status',
                                     'attempt_count',
                                     'success_streak',
                                     'last_score',
                                     'last_passed',
                                     'last_attempted_at',
                                     'scheduled_task_id',
                                     'scheduled_date',
                                     'created_at',
                                     'updated_at'),
                         'keys': ('id',),
                         'local_defaults': {}},
 'active_review_attempts': {'kind': 'review',
                            'columns': ('id',
                                        'review_item_id',
                                        'session_id',
                                        'plan_task_id',
                                        'mastery_evidence_id',
                                        'attempt_no',
                                        'score',
                                        'passed',
                                        'assistance_level',
                                        'evaluator_kind',
                                        'estimated_task_minutes',
                                        'completed_task_minutes',
                                        'actual_elapsed_minutes',
                                        'outcome',
                                        'reviewed_on',
                                        'next_interval_days',
                                        'next_due_date',
                                        'created_at'),
                            'keys': ('id',),
                            'local_defaults': {}},
 'orm_reviews': {'kind': 'review',
                 'columns': ('id', 'workspace_id', 'lesson_id', 'due_date', 'interval_days', 'status'),
                 'keys': ('id',),
                 'local_defaults': {}},
 'orm_review_attempts': {'kind': 'review',
                         'columns': ('id', 'workspace_id', 'lesson_id', 'score', 'feedback', 'created_at'),
                         'keys': ('id',),
                         'local_defaults': {}},
 'active_learner_profiles': {'kind': 'learner_profile',
                             'columns': ('learner_key', 'compact_profile', 'updated_at'),
                             'keys': ('learner_key',),
                             'local_defaults': {}},
 'orm_learner_preferences': {'kind': 'learner_profile',
                             'columns': ('id',
                                         'workspace_id',
                                         'preference_key',
                                         'value_json',
                                         'source',
                                         'status',
                                         'revision',
                                         'created_at',
                                         'updated_at'),
                             'keys': ('id',),
                             'local_defaults': {}},
 'active_receipts': {'kind': 'setting',
                     'columns': ('workspace_id', 'payload', 'updated_at'),
                     'keys': ('workspace_id',),
                     'local_defaults': {}},
 'active_workspace_settings': {'kind': 'setting',
                               'columns': ('workspace_id', 'archived', 'updated_at'),
                               'keys': ('workspace_id',),
                               'local_defaults': {}},
 'orm_material_dispositions': {'kind': 'setting',
                               'columns': ('id',
                                           'workspace_id',
                                           'source_id',
                                           'source_revision_id',
                                           'map_revision_id',
                                           'material_key',
                                           'disposition',
                                           'status',
                                           'reason',
                                           'metadata_json',
                                           'idempotency_key',
                                           'created_at',
                                           'decided_at',
                                           'updated_at'),
                               'keys': ('id',),
                               'local_defaults': {}},
 'orm_workspaces': {'kind': 'setting',
                    'columns': ('id',
                                'title',
                                'description',
                                'character_id',
                                'allow_external_knowledge',
                                'execution_mode',
                                'provider_name',
                                'provider_model',
                                'created_at'),
                    'keys': ('id',),
                    'local_defaults': {}},
 'orm_learning_runs': {'kind': 'learning_run',
                       'columns': ('id',
                                   'workspace_id',
                                   'task_id',
                                   'lesson_id',
                                   'phase',
                                   'status',
                                   'state',
                                   'revision',
                                   'created_at',
                                   'updated_at'),
                       'keys': ('id',),
                       'local_defaults': {'last_idempotency_key': '', 'last_response': ''}},
 'active_teaching_sessions': {'kind': 'learning_run',
                              'columns': ('id',
                                          'workspace_id',
                                          'plan_id',
                                          'state',
                                          'started_at',
                                          'updated_at',
                                          'actual_elapsed_minutes'),
                              'keys': ('id',),
                              'local_defaults': {}},
 'orm_learning_run_messages': {'kind': 'history',
                               'columns': ('id',
                                           'run_id',
                                           'role',
                                           'phase',
                                           'content',
                                           'grounding',
                                           'idempotency_key',
                                           'created_at'),
                               'keys': ('id',),
                               'local_defaults': {}},
 'active_teaching_messages': {'kind': 'history',
                              'columns': ('id',
                                          'session_id',
                                          'role',
                                          'action',
                                          'content',
                                          'citations',
                                          'attachment_ids',
                                          'created_at',
                                          'teacher_turn_json'),
                              'keys': ('id',),
                              'local_defaults': {}},
 'active_replan_history': {'kind': 'history',
                           'columns': ('id',
                                       'plan_id',
                                       'session_id',
                                       'workspace_id',
                                       'trigger_name',
                                       'reason',
                                       'affected_from',
                                       'before_snapshot',
                                       'after_snapshot',
                                       'moved_task_ids',
                                       'unscheduled_minutes',
                                       'created_at',
                                       'trigger',
                                       'before_state',
                                       'after_state'),
                           'keys': ('id',),
                           'local_defaults': {}},
 'orm_plan_activation_records': {'kind': 'task',
                                 'columns': ('id',
                                             'workspace_id',
                                             'plan_id',
                                             'previous_plan_id',
                                             'plan_revision',
                                             'status',
                                             'is_current',
                                             'plan_snapshot',
                                             'reason',
                                             'idempotency_key',
                                             'created_at',
                                             'activated_at'),
                                 'keys': ('id',),
                                 'local_defaults': {},
                                 'auxiliary': True},
 'active_knowledge_points': {'kind': 'task',
                             'columns': ('id',
                                         'workspace_id',
                                         'material_id',
                                         'normalized_name',
                                         'name',
                                         'explanation',
                                         'difficulty',
                                         'estimated_task_minutes',
                                         'mastery',
                                         'acceptance_method',
                                         'source_evidence',
                                         'isolated',
                                         'created_at'),
                             'keys': ('id',),
                             'local_defaults': {},
                             'auxiliary': True},
 'active_dependencies': {'kind': 'task',
                         'columns': ('id', 'workspace_id', 'prerequisite_id', 'dependent_id', 'evidence'),
                         'keys': ('id',),
                         'local_defaults': {},
                         'auxiliary': True},
 'orm_sources': {'kind': 'task',
                 'columns': ('id',
                             'workspace_id',
                             'title',
                             'source_type',
                             'trust_level',
                             'content_hash',
                             'status',
                             'page_count',
                             'language'),
                 'keys': ('id',),
                 'local_defaults': {'parse_error': '',
                                    'original_url': '',
                                    'local_path': '',
                                    'archive_preview_id': '',
                                    'archive_relative_path': '',
                                    'parent_archive_name': ''},
                 'auxiliary': True},
 'orm_source_revisions': {'kind': 'task',
                          'columns': ('id',
                                      'workspace_id',
                                      'source_id',
                                      'revision',
                                      'source_hash',
                                      'status',
                                      'created_at'),
                          'keys': ('id',),
                          'local_defaults': {},
                          'auxiliary': True},
 'orm_knowledge_map_revisions': {'kind': 'task',
                                 'columns': ('id',
                                             'map_id',
                                             'workspace_id',
                                             'revision',
                                             'is_current',
                                             'status',
                                             'generation_mode',
                                             'provider_used',
                                             'source_revisions',
                                             'anchor_material_id',
                                             'supporting_material_ids',
                                             'scope_policy',
                                             'role_provenance',
                                             'source_fingerprints',
                                             'quality',
                                             'fingerprint',
                                             'warnings',
                                             'staged_at',
                                             'activation_id',
                                             'activated_at',
                                             'created_at'),
                                 'keys': ('id',),
                                 'local_defaults': {'staging_token': '', 'failure_json': '{}'},
                                 'auxiliary': True},
 'orm_knowledge_nodes': {'kind': 'task',
                         'columns': ('id',
                                     'map_revision_id',
                                     'node_id',
                                     'source_id',
                                     'semantic_key',
                                     'label',
                                     'summary',
                                     'kind',
                                     'revision',
                                     'status',
                                     'content_hash',
                                     'source_refs',
                                     'history'),
                         'keys': ('id',),
                         'local_defaults': {},
                         'auxiliary': True},
 'orm_knowledge_edges': {'kind': 'task',
                         'columns': ('id',
                                     'map_revision_id',
                                     'edge_id',
                                     'source_node_id',
                                     'target_node_id',
                                     'relation',
                                     'source_refs'),
                         'keys': ('id',),
                         'local_defaults': {},
                         'auxiliary': True},
 'orm_milestones': {'kind': 'task',
                    'columns': ('id', 'plan_id', 'ordinal', 'title', 'objective'),
                    'keys': ('id',),
                    'local_defaults': {},
                    'auxiliary': True},
 'orm_modules': {'kind': 'task',
                 'columns': ('id', 'milestone_id', 'ordinal', 'title', 'objective'),
                 'keys': ('id',),
                 'local_defaults': {},
                 'auxiliary': True},
 'orm_lessons': {'kind': 'task',
                 'columns': ('id',
                             'module_id',
                             'ordinal',
                             'title',
                             'objective',
                             'estimated_minutes',
                             'completion_criteria'),
                 'keys': ('id',),
                 'local_defaults': {},
                 'auxiliary': True}}


RECORDS['active_plan_calendars'] = {
    'kind': 'calendar',
    'columns': ('plan_id','workspace_id','calendar','available_capacity_minutes','created_at'),
    'keys': ('plan_id',), 'local_defaults': {},
}


class ProductSyncRejected(ValueError):
    pass

from .product_sync_extensions import LEGACY_RECORDS, AGGREGATE_RECORDS
RECORDS.update(LEGACY_RECORDS)
RECORDS.update(AGGREGATE_RECORDS)


_PRIVATE = ('password', 'token', 'secret', 'credential', 'private_key', 'api_key',
            'provider_session', 'stored_path', 'local_path', 'file_path',
            'storage_path', 'absolute_path', 'resolved_path', 'local_uri', 'binary', 'raw_bytes')


def public_value(value):
    """Remove non-portable session/locator fields before durable intent creation."""
    if isinstance(value, dict):
        return {k: public_value(v) for k, v in value.items()
                if not any(word in str(k).lower() for word in _PRIVATE)}
    if isinstance(value, list):
        return [public_value(v) for v in value]
    if isinstance(value, str) and value.lstrip().startswith(('{', '[')):
        try:
            decoded = json.loads(value)
        except ValueError:
            return value
        return json.dumps(public_value(decoded), ensure_ascii=False, separators=(',', ':'))
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ProductSyncRejected('PRODUCT_SYNC_NONSTRUCTURED_VALUE')


def record_id(table, values):
    spec = RECORDS[table]
    keys = [values[k] for k in spec['keys']]
    if table == 'active_materials':
        return str(keys[0])
    digest = hashlib.sha256(json.dumps(keys, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return table + ':' + digest


def project(table, row):
    spec = RECORDS[table]
    values = {key: public_value(row[key]) for key in spec['columns']}
    if table == 'legacy_sources':
        from pathlib import PureWindowsPath
        values['original_filename'] = PureWindowsPath(values['original_filename']).name
        if values['source_type'] in {'upload','local_file','archive','archive_file'}:
            values['title'] = PureWindowsPath(values['title']).name
    record = dict(schema=SCHEMA, record_type=table, values=values)
    payload = {'product_record': record}
    if spec.get('auxiliary'):
        payload['product_auxiliary'] = True
    if spec['kind'] == 'resource':
        payload.update(product_classification=values.get('classification','USER_FILE'), filename=values['original_filename'] or values.get('title') or 'resource',
                       media_type=values.get('media_type', 'application/octet-stream'),
                       sha256=values.get('sha256', values.get('content_hash')), size_bytes=values.get('size_bytes', 0))
    elif spec['kind'] == 'plan':
        receipt = values.get('receipt') or '{}'
        receipt = json.loads(receipt) if isinstance(receipt, str) else receipt
        payload.update(title=str(values.get('title') or receipt.get('learning_goal') or receipt.get('goal') or values.get('id'))[:300], resource_ids=list(receipt.get('material_ids') or []) if table == 'active_plans' else [])
    elif spec['kind'] == 'progress':
        payload.update(plan_id=None, mastery=max(0., min(1., float(values.get('mastery') or values.get('score') or 0))),
                       completed_tasks=int(values.get('completed_task_count') or 0))
    validate(spec['kind'], record_id(table, values), payload)
    return payload


def validate(kind, entity_id, payload):
    record = payload.get('product_record')
    if record is None:
        # The wider sync route is not permission to send large untyped objects.
        if len(json.dumps(payload, allow_nan=False).encode()) > 256 * 1024:
            raise ProductSyncRejected('PRODUCT_SYNC_RECORD_TOO_LARGE')
        return
    if not isinstance(record, dict) or set(record) != {'schema', 'record_type', 'values'} or record['schema'] != SCHEMA:
        raise ProductSyncRejected('PRODUCT_SYNC_RECORD_SCHEMA')
    if not isinstance(record['record_type'],str):
        raise ProductSyncRejected('PRODUCT_SYNC_RECORD_SCHEMA')
    spec = RECORDS.get(record['record_type'])
    values = record['values']
    if not spec or spec['kind'] != kind or not isinstance(values, dict) or set(values) != set(spec['columns']):
        raise ProductSyncRejected('PRODUCT_SYNC_RECORD_FIELDS')
    if any(value is not None and (type(value) not in (str,int,float)
           or type(value) is float and not math.isfinite(value)) for value in values.values()):
        raise ProductSyncRejected('PRODUCT_SYNC_RECORD_VALUE')
    if any(type(values[k]) not in (str,int) or values[k] == '' for k in spec['keys']):
        raise ProductSyncRejected('PRODUCT_SYNC_RECORD_KEY')
    if payload.get('product_auxiliary',False) is not bool(spec.get('auxiliary')):
        raise ProductSyncRejected('PRODUCT_SYNC_AUXILIARY_MISMATCH')
    if record['record_type'] == 'active_materials' and any(
        payload.get(outer) != values[inner] for outer,inner in (
            ('filename','original_filename'),('sha256','sha256'),
            ('media_type','media_type'),('size_bytes','size_bytes'))):
        raise ProductSyncRejected('PRODUCT_SYNC_RESOURCE_MISMATCH')
    if public_value(values) != values:
        raise ProductSyncRejected('PRODUCT_SYNC_PRIVATE_FIELD')
    if record_id(record['record_type'], values) != entity_id:
        raise ProductSyncRejected('PRODUCT_SYNC_RECORD_ID')
    if len(json.dumps(payload, allow_nan=False).encode()) > record_byte_limit(record['record_type']):
        raise ProductSyncRejected('PRODUCT_SYNC_RECORD_TOO_LARGE')
    table=record['record_type']
    try:
        if table=='product_plan_state':
            from .product_runtime_state import validate_plan_record
            validate_plan_record(values['id'],json.loads(values['state']))
        elif table=='product_account_settings':
            from .product_settings_api import validate_setting
            validate_setting(values['id'],json.loads(values['state']))
        elif table=='product_review_state':
            from .product_runtime_state import restore_review
            restore_review({values['id']:json.loads(values['state'])})
    except (ValueError,TypeError,KeyError,AttributeError):
        raise ProductSyncRejected('PRODUCT_SYNC_STATE_INVALID') from None


# Measured retained aggregates reach 6,458,439 serialized bytes. These three
# typed domains alone receive 8 MiB; all schema/privacy/ID checks still apply.
LARGE_AGGREGATE_TYPES = frozenset({
    'active_plan_calendars', 'active_replan_history', 'orm_plan_activation_records',
})

def record_byte_limit(record_type):
    return 8 * 1024 * 1024 if record_type in LARGE_AGGREGATE_TYPES else 256 * 1024
