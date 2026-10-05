"""User selection, not publisher grading, authorizes original-text use."""
import json
import pytest
from refactor.learning.course import point_materials as pm, source_quality
from refactor.learning.course.question_bank import select_question
from refactor.learning.course.store import StoreError
from test_point_materials37_contract import store, ctx, pointer, TEXT, CONTRAST37


def forbidden56(*args, **kwargs):
    raise AssertionError('Selected material must never be publisher-graded')


@pytest.mark.parametrize('kind,url', [('file', None), ('url', 'https://unknown.example/mine'),
    ('url', 'https://github.com/myself/my-mistakes'), ('url', 'https://blog.csdn.net/myself/doc')])
def test_trusted56_selected_text_prepares_without_identity_or_scoring(store, monkeypatch, kind, url):
    source=store.records('sources','p')[0]
    store.add_sources('p',[dict(source,kind=kind,url=url,final_url=url,sha256='a'*64,user_provided=False)])
    monkeypatch.setattr(pm,'classify_source',forbidden56)
    monkeypatch.setattr(source_quality,'classify_source',forbidden56)
    quality=source_quality.annotate(store,'p',['s'])[0]
    assert quality['eligible'] and not quality['needs_review'] and quality['credibility'] is None
    assert '门槛' not in quality['note']
    class NoPublisher:
        complete=staticmethod(forbidden56)
    result=pm.prepare_point(store,'p',store.records('points','p')[0],dict(ctx(),gateway=NoPublisher(),compare_questions=True))
    assert result['questions']==2 and not pm.uncovered_points(store,'p',ctx())
    assert store.records('source_chunks','p')[0]['text']==TEXT


@pytest.mark.parametrize('url', [None, 'https://unknown.example/mine', 'https://zhidao.baidu.com/question/1'])
def test_trusted56_original_answered_questions_admitted_without_tier(store, url):
    extra='\n2. 压强的单位符号是____？\n答案：Pa。'
    store.add_sources('p',[dict(id='s',title='我的资料',kind='file' if url is None else 'url',url=url,
        adopted=True,sha256='a'*64,chunks=[dict(chunk_id='c',text=TEXT+extra,section=['压强']),
                            dict(chunk_id='other37',text=CONTRAST37,section=['运动学'])])])
    output=pointer()
    output['found']=[dict(kind='fill',stem=stem,answer=[[answer]],answer_quote=answer+'。',explanation='',
        question_chunk='c',answer_chunk='c',pairing='same_block')
        for stem,answer in [('压强单位是____？','帕'),('压强的单位符号是____？','Pa')]]
    result=pm.prepare_point(store,'p',store.records('points','p')[0],ctx(output))
    assert result['questions']==2 and result['cloze']==0
    assert all(q['origin']=='found' for q in store.records('question_bank','p') if q['role']=='bank')


@pytest.mark.parametrize('case,reason', [('empty','资料内容为空'),('unadopted','关联资料未采用')])
def test_trusted56_skipped_source_not_reported_as_absent(store,monkeypatch,case,reason):
    if case=='empty':
        records=store.records
        monkeypatch.setattr(store,'records',lambda table,plan:[dict(c,text='') if c['id']=='c' else c
            for c in records(table,plan)] if table=='source_chunks' else records(table,plan))
    else:store.set_source_adopted('p','s',False)
    with pytest.raises(StoreError) as error:
        pm.prepare_point(store,'p',store.records('points','p')[0],ctx())
    assert error.value.code=='CLOZE_FAILED' and error.value.source_unavailable and reason in str(error.value)
    evidence=list((store.root/'evidence/cloze').glob('*.json'))
    assert reason in json.loads(evidence[0].read_text(encoding='utf-8'))['rejected'][0]['reason']
    with pytest.raises(StoreError) as module_error:
        pm.prepare_module(store,'p','m',ctx(),lambda *args:None)
    assert module_error.value.code=='CLOZE_FAILED' and reason in str(module_error.value)
    # Read-only consumer compatibility: skipped text still uses the existing
    # classroom program-error branch, never a generic temporary material gap.
    from types import SimpleNamespace
    from refactor.learning.app.classroom import Classroom
    room=object.__new__(Classroom)
    room.services=SimpleNamespace(gateway=None)
    room._materials=lambda:pm
    course=SimpleNamespace(store=store,plan_id='p',point=lambda _:store.records('points','p')[0])
    result=room._prepare_one(course,'point',None)
    assert result['kind']=='question_program_error' and reason in result['reason']


def test_trusted56_selection_uses_history_not_publisher_grade(store):
    pm.prepare_point(store,'p',store.records('points','p')[0],ctx())
    rows=[q for q in store.records('question_bank','p') if q['role']=='bank']
    rows[0].update(source_level=1,uses=[dict(purpose='module',at='2026-10-05T00:00:00+00:00')])
    rows[1].update(source_level=3,uses=[])
    assert select_question(rows,'point','module')['id']==rows[1]['id']


def test_trusted56_legacy_quality_helpers_do_not_hide_or_rank_user_text():
    user=dict(user_provided=True,eligible=True,needs_review=False,credibility=None,relevance=None,note='用户资料')
    assert source_quality.lesson_selection([user],[])[0]['display']
    assert source_quality.rank_key(user)==source_quality.rank_key(dict(user,credibility=1,relevance=1))
    assert source_quality.apply_review(user,dict(relevance=1,note='不应按模型筛选')) is user


def test_trusted56_nonempty_selected_text_not_filtered_by_publisher_chunk_label(store):
    chunk=store._get('source_chunks','p','c')
    chunk['kind']='publisher_evidence'
    with store.transaction():store._put('source_chunks','p',chunk)
    assert pm.prepare_point(store,'p',store.records('points','p')[0],ctx())['questions']==2
