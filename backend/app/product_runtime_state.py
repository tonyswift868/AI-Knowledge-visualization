"""Typed snapshots of the existing Plan and Review runtimes.

Restore reconstructs data only: it never replays commands, calls a solver or
imports a class named by the payload. Runtime adapters remain the existing ones.
"""
from pydantic import TypeAdapter
from .planning.product_runtime.creation import PlanCreationOutcome
from .planning.product_runtime.contracts import PlanPublishConfirmation, PublishedPlan
from .planning.runtime_v2 import (PlanRuntime, ImmutablePlanSchedule, TaskCompletion,
    TaskSkip, PlanShiftPreview, PlanShiftConfirmation, PlanShiftApplication)


def _dump(items):
    return {key: value.model_dump(mode='json') for key, value in items.items()}


def _load(model, items):
    return {key: model.model_validate(value) for key, value in items.items()}


def export_plan(service):
    runtime = service.runtime
    states = {}
    for key, value in runtime._runtimes.items():
        shift = value._shift_service
        states[key] = dict(initial=value.initial_schedule.model_dump(mode='json'),
            current=value.current_schedule.model_dump(mode='json'),
            completions=_dump(value._completions), skips=_dump(value._skips),
            previews=_dump(shift._previews), confirmations=_dump(shift._confirmations),
            applications=_dump(shift._applications),
            preview_requests=[[list(k), v] for k, v in shift._preview_by_request.items()])
    return dict(schema='mentorflow.plan-runtime-state.v1',
        outcomes={k: TypeAdapter(PlanCreationOutcome).dump_python(v, mode='json')
                  for k, v in runtime._outcomes.items()},
        previews=runtime._preview_by_request_id, fingerprints=runtime._request_fingerprints,
        confirmations=_dump(runtime._confirmations), published=_dump(runtime._published),
        versions=runtime._publications_by_plan, runtimes=states,
        inputs=_dump(service._inputs_by_preview), schedules=service._published_schedule_json)


def restore_plan(state):
    from .plan_product_api import PlanProductApiService, PlanPreviewCreateIn
    if state['schema'] != 'mentorflow.plan-runtime-state.v1':
        raise ValueError('unknown plan state')
    service = PlanProductApiService()
    runtime = service.runtime
    runtime._outcomes = {k: TypeAdapter(PlanCreationOutcome).validate_python(v) for k,v in state['outcomes'].items()}
    runtime._preview_by_request_id = state['previews']
    runtime._request_fingerprints = state['fingerprints']
    runtime._confirmations = _load(PlanPublishConfirmation, state['confirmations'])
    runtime._published = _load(PublishedPlan, state['published'])
    runtime._published_by_preview = {v.preview_id: v for v in runtime._published.values()}
    runtime._publications_by_plan = state['versions']
    for key, data in state['runtimes'].items():
        value = PlanRuntime(ImmutablePlanSchedule.model_validate(data['initial']))
        value._current_schedule = ImmutablePlanSchedule.model_validate(data['current'])
        value._completions = _load(TaskCompletion, data['completions'])
        value._skips = _load(TaskSkip, data['skips'])
        shift = value._shift_service
        shift._previews = _load(PlanShiftPreview, data['previews'])
        shift._confirmations = _load(PlanShiftConfirmation, data['confirmations'])
        shift._applications = _load(PlanShiftApplication, data['applications'])
        shift._preview_by_request = {tuple(k): v for k,v in data['preview_requests']}
        runtime._runtimes[key] = value
    service._inputs_by_preview = _load(PlanPreviewCreateIn, state['inputs'])
    service._published_schedule_json = state['schedules']
    return service


def export_plan_records(service):
    """One immutable preview/publication or shift per record, not the whole account.

    Initial schedules are already owned by PublishedPlan. Do not duplicate them
    in every runtime snapshot or duplicate every historic preview on each edit.
    """
    full=export_plan(service)
    result={}
    for key,outcome in full['outcomes'].items():
        result['preview:'+key]=dict(type='preview',outcome=outcome,input=full['inputs'][key],
            fingerprint=full['fingerprints'][outcome['request']['request_id']])
    for key,published in full['published'].items():
        runtime=full['runtimes'][key]
        result['published:'+key]=dict(type='published',published=published,
            current=None if runtime['current']==runtime['initial'] else runtime['current'],
            completions=runtime['completions'],skips=runtime['skips'])
        for shift_id,preview in runtime['previews'].items():
            result['shift:'+shift_id]=dict(type='shift',publication_id=key,preview=preview,
                confirmation=runtime['confirmations'].get(shift_id),
                application=runtime['applications'].get(shift_id),
                requests=[item for item in runtime['preview_requests'] if item[1]==shift_id])
    return result


def restore_plan_records(records):
    from .plan_product_api import PlanProductApiService
    full=export_plan(PlanProductApiService())
    for state in records.values():
        if state['type']=='preview':
            outcome=state['outcome'];key=outcome['preview']['preview_id'];request=outcome['request']['request_id']
            full['outcomes'][key]=outcome;full['inputs'][key]=state['input']
            full['previews'][request]=key;full['fingerprints'][request]=state['fingerprint']
        elif state['type']=='published':
            published=state['published'];key=published['publication_id']
            full['published'][key]=published
            full['confirmations'][published['preview_id']]={'preview_id':published['preview_id'],
                'confirmation_id':published['confirmation_id']}
            full['runtimes'][key]=dict(initial=published['published_schedule'],
                current=state['current'] or published['published_schedule'],completions=state['completions'],
                skips=state['skips'],previews={},confirmations={},applications={},preview_requests=[])
            full['schedules'][key]=PublishedPlan.model_validate(published).published_schedule.model_dump_json()
            full['versions'].setdefault(published['plan_id'],[]).append(key)
    for state in records.values():
        if state['type']!='shift': continue
        runtime=full['runtimes'][state['publication_id']];key=state['preview']['preview_id']
        runtime['previews'][key]=state['preview']
        if state['confirmation']:runtime['confirmations'][key]=state['confirmation']
        if state['application']:runtime['applications'][key]=state['application']
        runtime['preview_requests'].extend(state['requests'])
    for versions in full['versions'].values():
        versions.sort(key=lambda key:full['published'][key]['publication_version'])
    return restore_plan(full)


def validate_plan_record(key, state):
    from .plan_product_api import PlanPreviewCreateIn
    shapes = {
        'preview': {'type','outcome','input','fingerprint'},
        'published': {'type','published','current','completions','skips'},
        'shift': {'type','publication_id','preview','confirmation','application','requests'},
    }
    if not isinstance(state,dict) or set(state)!=shapes.get(state.get('type')):
        raise ValueError('plan state fields')
    if state['type']=='preview':
        outcome=TypeAdapter(PlanCreationOutcome).validate_python(state['outcome'])
        PlanPreviewCreateIn.model_validate(state['input'])
        if key!='preview:'+outcome.preview.preview_id:raise ValueError('preview identity')
        from hashlib import sha256
        from .planning.product_runtime.creation import canonical_creation_request
        if state['fingerprint']!=sha256(canonical_creation_request(outcome.request).encode()).hexdigest():
            raise ValueError('preview fingerprint')
    elif state['type']=='published':
        published=PublishedPlan.model_validate(state['published'])
        if key!='published:'+published.publication_id:raise ValueError('publication identity')
        current=ImmutablePlanSchedule.model_validate(state['current']) if state['current'] else published.published_schedule
        if current.plan_id!=published.plan_id:raise ValueError('schedule plan identity')
        for model,name in ((TaskCompletion,'completions'),(TaskSkip,'skips')):
            for task,value in state[name].items():
                if model.model_validate(value).task_id!=task or task not in current.task_by_id:
                    raise ValueError('task identity')
    else:
        preview=PlanShiftPreview.model_validate(state['preview'])
        if key!='shift:'+preview.preview_id:raise ValueError('shift identity')
        if state['confirmation']:PlanShiftConfirmation.model_validate(state['confirmation'])
        if state['application']:PlanShiftApplication.model_validate(state['application'])


def export_review(service):
    # The DTOs are Pydantic; the remaining fixture fields have fixed simple types.
    result = {}
    for key in service._campaigns:
        fixture = service.export_recovery_fixture(key)
        result[key] = dict(resume_dto=_review_cursor_names(fixture.resume_dto.model_dump(mode='json')),
            observations=_dump(fixture.observations),
            accepted_proposal_ids=sorted(fixture.accepted_proposal_ids),
            local_only_operations=fixture.local_only_operations,
            question_started_at_ms=fixture.question_started_at_ms,
            hint_keys=[list(v) for v in sorted(fixture.hint_keys)],
            revealed_keys=[list(v) for v in sorted(fixture.revealed_keys)],
            last_result=fixture.last_result, last_round_result=fixture.last_round_result)
    return result


def _review_cursor_names(value, *, restoring=False):
    # The review DTO calls a non-secret read revision "position_token".
    # Use an unambiguous cursor name on the wire; secret filtering stays strict.
    source, target = ('revision_cursor','position_token') if restoring else ('position_token','revision_cursor')
    if isinstance(value,dict):
        return {target if k==source else k:_review_cursor_names(v,restoring=restoring) for k,v in value.items()}
    if isinstance(value,list): return [_review_cursor_names(v,restoring=restoring) for v in value]
    return value


def restore_review(states):
    from .review_product_v1.service import ReviewProductService, ReviewRecoveryFixture
    from .teaching.review_campaign_v2 import ReviewCampaignResumeDTO, ReviewObservation
    service = ReviewProductService()
    for key, value in states.items():
        value = dict(value)
        value['resume_dto'] = ReviewCampaignResumeDTO.model_validate(_review_cursor_names(value['resume_dto'],restoring=True))
        if value['resume_dto'].campaign.campaign_id != key:
            raise ValueError('review identity mismatch')
        value['observations'] = _load(ReviewObservation, value['observations'])
        value['accepted_proposal_ids'] = frozenset(value['accepted_proposal_ids'])
        value['hint_keys'] = frozenset(tuple(v) for v in value['hint_keys'])
        value['revealed_keys'] = frozenset(tuple(v) for v in value['revealed_keys'])
        service.restore_recovery_fixture(ReviewRecoveryFixture(**value))
    return service
