"""New concept scenes use existing account-synced positions and point credits."""
import copy
import json
from types import SimpleNamespace

import pytest

from refactor.learning.app.classroom import Classroom
from refactor.learning.course.store import CourseStore
from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, act, PLAN, ACCOUNT


def concept_environment(root):
    e=make_environment(root)
    e.store.add_sources(PLAN,[dict(id='extra',kind='url',url='https://example.invalid/concept',title='补充原文',adopted=True,
        chunks=[dict(chunk_id='c1',text='单位面积上的压力叫压强。',section=['定义']),
                dict(chunk_id='nav',text='推荐阅读资料书单。',section=['书单'])])])
    with e.store.transaction():
        p=e.store.records('points',PLAN)[0]
        p.update(structure='concept_v1',goal=p['summary'],chunk_ids=['c0','c1'],subpoints=[
            dict(id='p0-s1',title='比较面积',minutes=5,chunk_ids=['c0'],notes=[dict(text='面积越小，压强越大。',chunk_id='c0')]),
            dict(id='p0-s2',title='定义压强',minutes=5,chunk_ids=['c1'],notes=[dict(text='单位面积上的压力。',chunk_id='c1')]),
            dict(id='p0-s3',title='联系公式',minutes=5,chunk_ids=['c0','c1'],notes=[dict(text='p=F/S。',chunk_id='c0')])])
        e.store._put('points',PLAN,p)
        module=e.store.records('modules',PLAN)[0]
        module['navigation']=[dict(id='nav1',title='压强读物',chunk_ids=['nav'],reason='供拓展查阅')]
        e.store._put('modules',PLAN,module)
    e.requests=[]
    original=e.gateway.complete
    def complete(purpose,messages,**kwargs):
        e.requests.append((purpose,copy.deepcopy(messages),kwargs['system']))
        return original(purpose,messages,**kwargs)
    e.gateway.complete=complete
    return e


@pytest.fixture
def concept_env(tmp_path,monkeypatch):
    e=concept_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    yield e
    e.room.cancel_materials()
    e.store.close()


def task(e):
    return json.loads(next(m['content'].split('\n',1)[1] for m in reversed(e.requests[-1][1]) if m['content'].startswith('【本步任务】')))


def test_concept114_multiround_wrong_confused_continue_and_point_credit(concept_env):
    e=concept_env
    view=act(e,'next')
    assert view['step']=='teaching' and view['concept']['number']==1
    assert not view['conversation']['options']
    act(e,'reply','面积越小，压强越小。')
    view=act(e,'confused')
    assert view['concept']['number']==1 and view['concept']['rounds']==3
    assert not e.store.records('credits',PLAN) and not e.store.records('judgements',PLAN)
    assert task(e)['concept']['subpoint_id']=='p0-s1'
    view=act(e,'reply','懂了')
    assert view['concept']['number']==2
    assert task(e)['concept']['notes'][0]['chunk_id']=='c1'
    originals=next(m['content'] for m in reversed(e.requests[-1][1]) if m['content'].startswith('【当前模块原文'))
    assert '单位面积' in originals and 'c0' not in originals and '书单' not in originals
    assert '不要求每轮提问' in e.requests[-1][2]
    view=act(e,'next');assert view['concept']['number']==3
    before=len(e.gateway.calls)
    view=act(e,'next');assert view['step']=='test_offer' and view['concept'] is None
    credits=e.store.records('credits',PLAN)
    assert len(credits)==1 and credits[0]['credit']['minutes']==15
    assert len(e.gateway.calls)==before and view['messages'][-1]['kind']=='recap'
    assert len(view['messages'][-1]['recap']['items'])==3
    assert not e.store.records('judgements',PLAN)


def test_concept114_reopen_and_second_device_continue_same_subpoint(concept_env,tmp_path):
    e=concept_env
    act(e,'next');act(e,'next');before=act(e,'reply','这一步我还想多聊。')
    e.room=Classroom(e.services,e.room.study_day,now=e.room.now)
    reopened=act(e,'enter')
    assert reopened['concept']==before['concept']
    other=CourseStore(ACCOUNT,tmp_path/'second')
    try:
        other.apply_remote([(record,i+1) for i,record in enumerate(e.store.pending_records(10000))])
        services=SimpleNamespace(**vars(e.services));services.store=other;services.data_root=tmp_path/'second'
        room=Classroom(services,e.room.study_day,now=e.room.now)
        view=room.perform(PLAN,'enter',None)
        assert not view['failure'] and view['concept']==before['concept']
        assert view['step']=='teaching'
    finally:other.close()


def test_concept114_navigation_and_notes_are_view_only(concept_env):
    e=concept_env;view=act(e,'enter')
    assert view['module']['navigation'][0]['sources'][0]['id']=='nav'
    assert view['concept']['notes'][0]['source']['id']=='c0'
    assert not e.gateway.calls and not e.store.records('credits',PLAN)
    assert all(q['point_ids'] in (['p0'],['p1']) for q in e.materials.bank)
    act(e,'next')
    assert '推荐阅读资料书单' not in json.dumps(e.requests,ensure_ascii=False)


def test_concept114_legacy_point_has_original_opening_flow(tmp_path,monkeypatch):
    e=make_environment(tmp_path)
    monkeypatch.setattr(Classroom,'_materials',staticmethod(lambda:e.materials))
    try:
        view=act(e,'next');assert view['step']=='opening' and view['concept'] is None
        assert view['conversation']['options']==['针尖','手指','不知道','直接开始讲']
        assert act(e,'choose',0)['step']=='teaching'
        assert act(e,'next')['step']=='test_offer'
        assert len(e.store.records('credits',PLAN))==1
    finally:e.store.close()
