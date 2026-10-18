"""User materials are adopted and repository aliases do not become new picks."""
import copy


import pytest

from tests.refactor.plan51.test_plan51_selected_material_flow import flow
from tests.refactor.plan_additions.test_addition_links import PAGE, REPO, page, repo_fetch


@pytest.mark.parametrize('kind', ['repo', 'url', 'file'])
def test_source170_user_material_is_adopted_and_reopens(flow, tmp_path, kind):
    f, ctx, pid, _ = flow
    f.repo_fetch = repo_fetch([])
    f.fetcher = lambda url, **kwargs: page(url)
    if kind == 'file':
        path = tmp_path/'lesson.md'
        path.write_text('# 入门课\n\n'+('队列是先进先出的结构，栈是后进先出的结构。'*25), encoding='utf-8')
        f.add_file(pid, path)
    else:
        f.run(pid, lambda d,c: f.chat(d,c,REPO if kind == 'repo' else PAGE), ctx)
    d = f.get_draft(pid)
    card = d['sources'][-1]
    assert card['status'] == 'adopted' and card['user_provided']
    assert card['source_id'] and card['chars'] > 0
    assert len(f._adopted(f.load(pid))[0]) == 1
    assert f.services.store.records('sources',pid)[0]['adopted']
    # An explicit rejection remains rejected on reads; default does not mean forced.
    f.run(pid, lambda d,c: f.decide(d,card['card_id'],False,ctx=c), ctx)
    assert f.get_draft(pid)['sources'][-1]['status'] == 'rejected'


def test_source170_repasted_recommendation_becomes_visible_own_adopted_card(flow):
    f, ctx, pid, _ = flow
    f.repo_fetch = repo_fetch([])
    f.run(pid,lambda d,c:f.chat(d,c,REPO),ctx)
    d=f.load(pid)
    own=d['sources'][0]
    own.update(user_provided=False,status='rejected')
    d['recommendations']=[dict(copy.deepcopy(own),status='new')]
    f.services.store.set_source_adopted(pid,own['source_id'],False)
    f.save(d)
    count=len(f.services.store.records('source_chunks',pid))
    view=f.run(pid,lambda d,c:f.chat(d,c,REPO),ctx)['draft']
    card=view['sources'][0]
    assert card['status']=='adopted' and card['user_provided']
    assert view['messages'][-1]['cards']==[card['card_id']]
    assert view['recommendations']==[]
    assert len(f.services.store.records('source_chunks',pid))==count
    assert f._adopted(f.load(pid))[0]


def test_source170_new_and_old_duplicate_recommendations_are_filtered(flow, monkeypatch):
    f, ctx, pid, _ = flow
    f.repo_fetch=repo_fetch([])
    f.run(pid,lambda d,c:f.chat(d,c,REPO),ctx)
    d=f.load(pid)
    d['recommendations']=[dict(card_id='old',url=REPO+'/tags',title='版本标签',status='new')]
    f.save(d)
    distinct='https://github.com/another/lesson'
    rows=[dict(url=u,title=u,type='仓库',why='学习资料',modules=['基础'])
          for u in (REPO,REPO+'/tree/main/docs',REPO+'/releases',distinct)]
    monkeypatch.setattr(f,'_ask',lambda *a,**k:(rows,None))
    checked=[]
    f.fetcher=lambda url,**kw: (checked.append(url) or page(url))
    view=f.run(pid,lambda d,c:f._recommend(d,c),ctx)['draft']
    assert [c['url'] for c in view['recommendations']]==[distinct]
    assert checked==[distinct]
    assert view['sources'][0]['status']=='adopted'


def test_source170_pasted_page_prunes_old_exact_url_duplicate(flow):
    # The normal page and repository readers are still independent imports.
    f, ctx, pid, _=flow
    f.fetcher=lambda url,**kw:page(url)
    d=f.load(pid)
    d['recommendations']=[dict(card_id='old-page',url=PAGE,title='旧推荐',status='new')]
    f.save(d)
    view=f.run(pid,lambda d,c:f.chat(d,c,PAGE),ctx)['draft']
    assert view['recommendations']==[] and view['sources'][0]['status']=='adopted'
