from datetime import date, timedelta, datetime, timezone
from datetime import time
from zoneinfo import ZoneInfo

from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course.mastery import AttemptClosed, Judgement
from refactor.learning.course.store import CourseStore, StoreError
from tests.refactor.mastery70.support import seed_assessment


def prepared_store(tmp_path):
    store=CourseStore('grant-account',tmp_path)
    store.create_plan('plan',goal='目标',final_goal='最终目标',level='入门',notes='',
                      window=PlanWindow(date(2026,10,27),date(2026,11,3),60))
    store.add_sources('plan',[dict(id='source',kind='url',url='https://example.invalid/',title='资料',site='测试',
        tier='official',credibility=1.0,adopted=True,chunks=[dict(chunk_id='chunk',text='原文',page_start=None,page_end=None,section=[])])])
    store.save_outline('plan',[dict(id='stage',title='阶段')],
        [dict(id='module',stage_id='stage',title='模块',prerequisites=[])])
    store.save_generated_module('plan','module',[
        dict(id='point',summary='掌握目标',minutes=30,chunk_ids=['chunk'])],check_minutes=2,today=date(2026,10,27))
    seed_assessment(store,'plan')
    return store


def test_grant_api_is_synced_idempotent_and_does_not_create_answer_or_credit(tmp_path):
    store=prepared_store(tmp_path)
    try:
        q=store.records('question_bank','plan')[0]
        event=Judgement('wrong-answer',1,datetime(2026,10,27,tzinfo=timezone.utc),
                        'module_test','module','attempt',((q['point_ids'][0],False),),question_id=q['id'])
        store.append_judgement('plan',event)
        store.append_attempt_closed('plan',AttemptClosed('closed',2,'module','attempt','module_test'),
            details={'assessment_id':'attempt','question_ids':[q['id']]})
        assert store.load_mastery('plan').module('module').state=='failed'

        preview=store.preview_questions_as_passed('plan','module',[q['id']])
        assert preview['modules']['module']['state']=='passed'
        assert store.load_mastery('plan').module('module').state=='failed'  # preview wrote nothing.
        first=store.grant_questions_as_passed('plan','module',[q['id']],grant_id='grant-1')
        second=store.grant_questions_as_passed('plan','module',[q['id']],grant_id='grant-1')
        assert first['newly_granted']==[q['id']]
        assert second['already_granted']==[q['id']]
        try:
            store.grant_final_check_passed('plan',grant_id='grant-1',today=date(2026,10,27))
        except StoreError as exc:
            assert exc.code=='OVERRIDE_CONFLICT'  # IDs cannot be reused across grant modes.
        else:
            raise AssertionError('cross-mode grant ID reuse must conflict')
        assert store.load_mastery('plan').module('module').state=='passed'
        assert store.load_mastery('plan').stats()['questions'][q['id']]['module_errors']==1
        assert store.records('question_answers','plan')==[]
        assert store.records('credits','plan')==[]
        wrong_groups=store.wrong_answers('plan')
        assert len(wrong_groups)==1 and len(wrong_groups[0]['attempts'])==1
        grants=[r for r in store.records('judgements','plan') if r.get('details',{}).get('user_override')]
        assert len(grants)==1 and grants[0]['details']['override_kind']=='question_passed'
        assert any(r.entity=='judgements' and r.payload.get('details',{}).get('grant_id')=='grant-1'
                   for r in store.pending_records(100))

        remote=CourseStore('grant-remote',tmp_path/'remote')
        try:
            sent=store.pending_records(1000)
            remote.apply_remote([(row,index+1) for index,row in enumerate(sent)])
            assert remote.load_mastery('plan').module('module').state=='passed'
            assert remote.records('question_answers','plan')==[]
            assert remote.records('credits','plan')==[]
        finally:
            remote.close()

        # A regular final-check override is refused until every module passed.
        other=store.load_mastery('plan')
        assert other.check_ready()
        final=store.grant_final_check_passed('plan',grant_id='final-1',today=date(2026,10,27))
        assert final['state']=='completed'
        assert store.load_mastery('plan').check_complete()
    finally:
        store.close()


def test_final_grant_refuses_failed_module_without_creating_override(tmp_path):
    store=prepared_store(tmp_path)
    try:
        try:
            store.grant_final_check_passed('plan',grant_id='final-blocked',today=date(2026,10,27))
        except ValueError as exc:
            assert getattr(exc,'code',None)=='CHECK_NOT_READY'
        else:
            raise AssertionError('final grant must refuse while modules are incomplete')
        assert not any(r.get('details',{}).get('user_override') for r in store.records('judgements','plan'))
    finally:
        store.close()


def test_manual_grant_is_not_counted_as_study_activity(tmp_path):
    store=prepared_store(tmp_path)
    try:
        q=store.records('question_bank','plan')[0]
        store.grant_questions_as_passed('plan','module',[q['id']],grant_id='activity-only-grant')
        assert not store.has_study_activity('plan', day=date(2026,10,27), refresh=time(4),
                                            tz=ZoneInfo('UTC'))
    finally:
        store.close()

