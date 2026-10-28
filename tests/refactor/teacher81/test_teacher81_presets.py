"""Preset management through production handlers, real store, classroom and sync."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from refactor.learning.app import handlers
from refactor.learning.app.bridge import BridgeError
from refactor.learning.app.services import AppServices
from refactor.learning.app.settings import AppSettings, DEFAULTS, teaching_preferences, validate_presets
from refactor.learning.course.store import CourseStore
from refactor.learning.course.sync_course import CourseSync
from refactor.learning.course.ledger import PlanWindow
from refactor.learning.course import teacher_agent, profile_agent
from tests.refactor.app.test_app_window_layer import qapp, tmp_root, make_bridge, pump, _Window
from tests.refactor.app.test_app_sync_lifecycle import window, spin
from tests.refactor.app.test_app_plan_flow import form, ScriptedModel
from tests.refactor.integration38.support import Agents, join_workers
from tests.refactor.course_integration.test_store_sync_roundtrip import connection

ACCOUNT='mfa1_'+'8'*64
PLAN='8'*32


def preset(identifier='teacher-2', **changes):
    return dict(id=identifier,name='相声老师',persona='说话像相声演员',style='humorous',detail='brief',
                examples='normal',analogies=['life'],**{}) | changes


@pytest.fixture
def wired(tmp_path,qapp):
    services=AppServices(account_id=ACCOUNT,data_root=tmp_path,session_provider=lambda:None,
                         api_base_url='https://api.invalid',ca_bundle=tmp_path/'unused.pem')
    bridge,sent=make_bridge(qapp)
    handlers.register(bridge,_Window(services))
    counter=iter(range(1000))
    def call(method,**params):
        identifier='preset'+str(next(counter))
        bridge.call(json.dumps(dict(id=identifier,method=method,params=params)))
        return pump(qapp,bridge,sent,lambda m:m.get('id')==identifier and m['type']=='response',timeout=20)[0]
    yield services,call
    bridge.close();join_workers();services.close()


def ok(call,method,**params):
    response=call(method,**params)
    assert response['ok'],response
    return response['result']


def test_teacher81_legacy_migration_and_validation(tmp_path):
    settings=AppSettings(ACCOUNT,tmp_path)
    legacy={k:v for k,v in DEFAULTS.items() if k not in ('teacher_presets','default_teacher_preset_id')}
    legacy.update(teacher_style='strict',detail='detailed',examples='many',analogy_domains=['games'])
    settings._file.write(legacy)
    group=settings.teacher_presets()
    assert group['default_id']=='teacher-1'
    assert group['presets']==[dict(id='teacher-1',name='预设一',persona='',style='strict',detail='detailed',examples='many',analogies=['games'])]
    for rows,default in (([], 'x'),([preset()]*2,'teacher-2'),([preset(str(i),name=str(i)) for i in range(5)],'0'),
                         ([preset(name=' ')],'teacher-2'),([preset(persona='字'*2001)],'teacher-2'),
                         ([preset(),'invalid'],'teacher-2'),([preset()],None)):
        with pytest.raises(BridgeError):validate_presets(rows,default)


def test_teacher81_formal_handlers_crud_binding_delete_and_fallback(wired):
    services,call=wired
    group=ok(call,'settings.teacher_presets')
    rows=[*group['presets'],preset(),preset('teacher-3',name='三'),preset('teacher-4',name='四')]
    ok(call,'settings.save_teacher_presets',presets=rows,default_id='teacher-1')
    assert not call('settings.save_teacher_presets',presets=[*rows,preset('fifth',name='五')],default_id='teacher-1')['ok']
    services.store.create_plan(PLAN)
    assert ok(call,'plan.detail',plan_id=PLAN)['teacher_preset']==dict(id='teacher-1',name='预设一')
    ok(call,'plan.set_teacher_preset',plan_id=PLAN,preset_id='teacher-2')
    assert ok(call,'plan.detail',plan_id=PLAN)['teacher_preset']['name']=='相声老师'
    rows[1]=preset(name='改名',persona='今天说相声')
    ok(call,'settings.save_teacher_presets',presets=rows,default_id='teacher-1')
    assert ok(call,'plan.detail',plan_id=PLAN)['teacher_preset']['name']=='改名'
    removal=ok(call,'settings.save_teacher_presets',presets=[rows[0],*rows[2:]],default_id='teacher-1')
    assert removal['confirmation_required'] and removal['usage_count']==1
    assert services.store.get_plan(PLAN)['teacher_preset_id']=='teacher-2'
    ok(call,'settings.save_teacher_presets',presets=[rows[0],*rows[2:]],default_id='teacher-1',confirm_delete=True)
    assert services.store.get_plan(PLAN)['teacher_preset_id']=='teacher-1'
    assert call('settings.save_teacher_presets',presets=rows[2:],default_id='teacher-3')['error']['code']=='DEFAULT_PRESET'
    ok(call,'settings.save_teacher_presets',presets=[rows[0],*rows[2:]],default_id='teacher-3')
    ok(call,'settings.save_teacher_presets',presets=rows[2:],default_id='teacher-3',confirm_delete=True)
    # Stale/missing IDs use the current default, including old plans without a field.
    assert ok(call,'plan.detail',plan_id=PLAN)['teacher_preset']['id']=='teacher-3'
    assert not call('plan.set_teacher_preset',plan_id=PLAN,preset_id='gone')['ok']
    assert not call('settings.save',changes={'teacher_presets':rows})['ok']


def test_teacher81_new_plan_saves_current_default_via_handlers(wired):
    services,call=wired
    first=ok(call,'settings.teacher_presets')['presets'][0]
    ok(call,'settings.save_teacher_presets',presets=[first,preset()],default_id='teacher-2')
    services._gateway=ScriptedModel()
    draft=ok(call,'plan.current')['draft']
    ok(call,'plan.submit',plan_id=draft['plan_id'],form=form(date.today()))
    assert services.store.get_plan(draft['plan_id'])['teacher_preset_id']=='teacher-2'


def seed(store):
    today=date.today()
    store.create_plan(PLAN,goal='先进先出',final_goal='理解队列',level='beginner',window=PlanWindow(today,today+timedelta(days=6),120))
    store.add_sources(PLAN,[dict(id='s',url='https://docs.python.org/3/tutorial/',adopted=True,
        chunks=[dict(chunk_id='c',text='队列是先进先出的结构。')])])
    store.save_outline(PLAN,[dict(id='st',title='基础')],[dict(id='m',stage_id='st',title='队列')])
    store.save_generated_module(PLAN,'m',[dict(id='p',title='队列',summary='先进先出',minutes=15,chunk_ids=['c'])],check_minutes=0,today=today)
    store.save_module_preparation(PLAN,'m',dict(state='done',questions=0))
    store.start_plan(PLAN,today=today)


def test_teacher81_switch_next_classroom_request_and_stable_system_prefix(wired):
    services,call=wired
    first=ok(call,'settings.teacher_presets')['presets'][0]
    ok(call,'settings.save_teacher_presets',presets=[first,preset()],default_id='teacher-1')
    seed(services.store);agents=services._gateway=Agents()
    ok(call,'classroom.enter',plan_id=PLAN)
    ok(call,'classroom.next',plan_id=PLAN)
    initial=agents.requests[-1]['system']
    prefix=deepcopy(agents.requests[-1]['messages'])
    ok(call,'classroom.confused',plan_id=PLAN)
    assert agents.requests[-1]['system']==initial
    assert agents.requests[-1]['messages'][:len(prefix)]==prefix
    reply=ok(call,'classroom.ask',plan_id=PLAN,text='请解释队列。')
    assert not reply.get('failure')
    qa_initial=agents.requests[-1]['system']
    assert '主动答疑协议' in qa_initial and '教学规则始终优先于人设' in qa_initial
    ok(call,'plan.set_teacher_preset',plan_id=PLAN,preset_id='teacher-2')
    ok(call,'classroom.ask',plan_id=PLAN,text='请再解释一句。')
    changed=agents.requests[-1]['system']
    assert changed!=qa_initial and '说话像相声演员' in changed and '教学规则始终优先于人设' in changed
    ok(call,'classroom.ask',plan_id=PLAN,text='请再举一句例子。')
    assert agents.requests[-1]['system']==changed
    from refactor.learning.app.classroom import Classroom
    room=Classroom(services,date.today)
    room._profile(room._course(PLAN))
    assert services.store.records('teacher_profile',PLAN)[0]['document']['preferences']['persona']=='说话像相声演员'
    assert '说话像相声演员' in profile_agent._preference_text(teaching_preferences(services.settings,services.store,PLAN))
    assert all('chat_persona' not in r['system'] for r in agents.requests)


@pytest.mark.parametrize('incremental',[False,True])
def test_teacher81_two_devices_presets_and_plan_binding_real_course_sync(tmp_path,connection,incremental):
    server,transport,login=connection
    server.incremental=incremental
    account=login.identity.account_id
    with CourseStore(account,tmp_path/'a') as a,CourseStore(account,tmp_path/'b') as b:
        sa=AppSettings(account,tmp_path/'a',store_provider=lambda:a)
        sb=AppSettings(account,tmp_path/'b',store_provider=lambda:b)
        first=sa.teacher_presets()['presets'][0]
        sa.save_teacher_presets([first,preset()],'teacher-2',a)
        a.create_plan(PLAN,teacher_preset_id='teacher-2')
        sb.teacher_presets()  # independently migrated second device must not overwrite A
        CourseSync(a,transport).sync();CourseSync(b,transport).sync()
        assert sa.teacher_presets()==sb.teacher_presets()
        assert b.get_plan(PLAN)['teacher_preset_id']=='teacher-2'
        sb.save_teacher_presets([first,preset(persona='另一台电脑编辑')],'teacher-2',b)
        b.set_teacher_preset(PLAN,'teacher-1')
        CourseSync(b,transport).sync();CourseSync(a,transport).sync()
        assert sa.teacher_presets()==sb.teacher_presets()
        assert a.get_plan(PLAN)['teacher_preset_id']=='teacher-1'
        sb.save_teacher_presets([first,preset()],'teacher-1',b)
        sb.save_teacher_presets([first],'teacher-1',b,confirm_delete=True)
        CourseSync(b,transport).sync();CourseSync(a,transport).sync()
        assert sa.teacher_presets()==dict(presets=[first],default_id='teacher-1')
        assert not a.pending_records(100)


def test_teacher81_remote_settings_refresh_production_window(window,qapp,tmp_root):
    win,server,transport,login,_,_,_,sent=window
    with CourseStore(login.identity.account_id,tmp_root/'remote') as remote:
        settings=AppSettings(login.identity.account_id,tmp_root/'remote',store_provider=lambda:remote)
        first=settings.teacher_presets()['presets'][0]
        settings.save_teacher_presets([first,preset()],'teacher-2',remote)
        CourseSync(remote,transport).sync()
        win.sync_now()
        spin(qapp,win,lambda:any(m.get('name')=='settings' and m['data'].get('default_teacher_preset_id')=='teacher-2' for m in sent)
             and win._sync.service._future is None)
        assert win.services.settings.teacher_presets()['default_id']=='teacher-2'


def test_teacher81_v5_database_upgrade_preserves_plan(tmp_path):
    with CourseStore(ACCOUNT,tmp_path) as store:
        store.create_plan(PLAN)
        store._db.execute('DROP TABLE account_settings')
        store._db.execute('UPDATE schema_version SET version=5')
    with CourseStore(ACCOUNT,tmp_path) as store:
        settings=AppSettings(ACCOUNT,tmp_path,store_provider=lambda:store)
        assert settings.teacher_presets()['default_id']=='teacher-1'
        assert store.get_plan(PLAN)['id']==PLAN
