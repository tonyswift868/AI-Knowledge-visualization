"""Two points in one module; real store/classroom, fake model and material IO."""
import copy

from tests.refactor.concept114.test_concept114_classroom import concept_environment
from tests.refactor.conversation37.test_grounded_conversation37 import question, PLAN, DAY


def environment130(root, *, next_concept=True):
    e=concept_environment(root)
    point=copy.deepcopy(e.store.records('points',PLAN)[0])
    following=dict(id='p2',module_id='m0',position=1,title='压强与生活',summary='根据面积比较压强',minutes=15,chunk_ids=['c0'])
    if next_concept:
        following.update(structure='concept_v1',goal=following['summary'],subpoints=[
            dict(id='p2-s1',title='受力面积',minutes=8,chunk_ids=['c0'],notes=[dict(text='压力相同时，面积越小压强越大。',chunk_id='c0')]),
            dict(id='p2-s2',title='应用公式',minutes=7,chunk_ids=['c0'],notes=[dict(text='用 p=F/S 比较压强。',chunk_id='c0')])])
    # Complete the synthetic outline before returning an active fixture.
    with e.store.transaction():
        plan=e.store.get_plan(PLAN);plan['state']='ready';e.store._put('plans',PLAN,plan)
        e.store.save_generated_module(PLAN,'m0',[point,following],check_minutes=5,today=DAY)
        plan=e.store.get_plan(PLAN);plan['state']='active';e.store._put('plans',PLAN,plan)
    q=question('p2');q['module_id']='m0';e.materials.bank.append(q)
    e.room._materials=lambda:e.materials
    return e


def last_subpoint130(e):
    from tests.refactor.conversation37.test_grounded_conversation37 import act
    act(e,'next');act(e,'next')
    view=act(e,'next')
    assert view['concept']['subpoint_id']=='p0-s3'
    return view


def close130(e):
    e.room.cancel_materials()
    e.store.close()
