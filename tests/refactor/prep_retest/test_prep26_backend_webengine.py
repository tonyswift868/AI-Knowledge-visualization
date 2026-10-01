"""Built product React + real LearningWindow/QWebChannel/Classroom, fake model only.

No login, sync, catalog refresh or paid API is invoked. All account files are
isolated. Run this file with --artifacts to retain step screenshots on D:.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

from tests.refactor.guided_lessons.fixtures import wire

REPO = Path(__file__).resolve().parents[3]
PLAN = 'b' * 32


def build(directory):
    local = Path('D:/MentorFlow/.local')
    env = dict(os.environ, PATH=str(local/'node')+os.pathsep+os.environ.get('PATH',''),
               MENTORFLOW_WEB_DEPS=str(local/'web-deps'))
    directory.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(local/'node/node.exe'), str(local/'web-deps/node_modules/vite/bin/vite.js'),
        'build','--config',str(REPO/'refactor/learning/web/vite.config.mjs'), '--outDir',str(directory/'web-dist')],
        cwd=REPO,env=env,capture_output=True,text=True,encoding='utf-8',timeout=100)
    (directory/'build.log').write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode==0, result.stdout+result.stderr


class FakeGateway:
    """Fixed parsed model contracts; does not replace bridge or classroom methods."""
    def __init__(self):
        self.calls=[]
        self.price_catalog=SimpleNamespace(_period=lambda at:'peak')
    def get_configuration(self): return {'provider_id':'deepseek'}
    def close(self): pass
    def complete(self,purpose,messages,**kwargs):
        self.calls.append(purpose)
        from tests.refactor.question_solver.fakes import response, grade, is_solver_judgement
        extra = response(purpose, messages)
        if extra is not None: return extra
        if purpose == 'judge' and is_solver_judgement(messages): return grade(messages)
        if purpose=='lesson_prep':
            task=json.loads(messages[-1]['content'].split('\n',1)[1])
            value=wire('c')
        elif purpose=='lesson_review':
            # Intentionally force bounded fallback on the second point.
            value=dict(verdict='live' if '第二点' in messages[-1]['content'] else 'approve',
                issues=['固定替身要求现场讲'] if '第二点' in messages[-1]['content'] else [],revised=None)
        elif purpose=='teacher' and kwargs['system'].startswith('现场分步'):
            value=wire('c',live=True)
        elif purpose=='teacher':
            value='这次插话我们先放在这里，点回到刚才继续。' if '插话' in kwargs['system']+str(messages) else '这题需要按进入顺序来想。'
        elif purpose=='questions':
            text=messages[-1]['content']
            points=json.JSONDecoder().raw_decode(text.split('【知识点清单】\n')[1])[0]
            value={'questions':[dict(id=f'{p["id"]}-{a}',type='choice',stem=f'固定检测{a}：{p["title"]}按哪个顺序取出？',
                options=['先进入的先取出','后进入的先取出','随机取出','同时取出'],answer='先进入的先取出',
                reference='先入先出。',point_ids=[p['id']],chunk_ids=['c'],angle=a)
                for p in points for a in ('recall','apply')]}
        elif purpose=='question_review':
            value=dict(verdict='approve',issues=[],revised=None)
        else: raise AssertionError('Unexpected fake purpose: '+purpose)
        text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
        if kwargs.get('on_text'): kwargs['on_text'](text)
        return SimpleNamespace(text=text)


def browser(directory):
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu')
    from PySide6.QtWidgets import QApplication
    from refactor.learning.app import window as wm
    from refactor.learning.app.services import AppServices
    from refactor.learning.app.classroom import Classroom
    from refactor.learning.course.ledger import PlanWindow
    from refactor.learning.course.mastery import Judgement, AttemptClosed
    from refactor.learning.course.report import build_report
    from datetime import datetime, timezone

    directory=Path(directory); (directory/'screenshots').mkdir(exist_ok=True)
    app=QApplication([])
    services=AppServices(account_id='prep26_synthetic',data_root=directory/f'account-{time.time_ns()}',session_provider=lambda:None,
                         api_base_url='https://example.invalid',ca_bundle=None)
    gateway=FakeGateway(); services._gateway=gateway
    services.settings.save(dict(detail='brief',reduce_motion=True))
    store=services.store; day=date.today()
    store.create_plan(PLAN,goal='真实后端分步联调',window=PlanWindow(day,day,200))
    store.add_sources(PLAN,[dict(id='s',title='合成队列资料',kind='url',url='https://example.invalid',
                                chunks=[dict(chunk_id='c',text='队列先进入的先取出。')])])
    store.save_outline(PLAN,[dict(id='st',title='顺序')],[dict(id='m',stage_id='st',title='队列')])
    store.save_generated_module(PLAN,'m',[dict(id=f'p{i}',title=f'第{i+1}点' if i==0 else '第二点',summary='先入先出',
        minutes=15,chunk_ids=['c']) for i in range(2)],check_minutes=8,today=day)
    store.start_plan(PLAN,today=day)
    events=[]; checks=[]
    with patch.object(wm,'WEB_ROOT',directory/'web-dist'), \
         patch.object(Classroom,'_prepare_later',lambda *a,**k:None), \
         patch.object(Classroom,'_schedule_idle_prep',lambda *a,**k:None), \
         patch.object(Classroom,'_prefetch_banks',lambda *a,**k:None), \
         patch.object(Classroom,'_profile_later',lambda *a,**k:None):
        window=wm.LearningWindow('合成身份','LEARNER','example.invalid',account_id=services.account_id,data_root=services.data_root)
        window.services=services; window.resize(1440,900); window.show()
        window.bridge.event.connect(lambda data: events.append(json.loads(data)))
        def pump(seconds=.05):
            end=time.monotonic()+seconds
            while time.monotonic()<end: app.processEvents();time.sleep(.005)
        def js(code):
            result=[];window.page.runJavaScript(code,result.append)
            deadline=time.monotonic()+5
            while not result and time.monotonic()<deadline:pump()
            assert result,'JS timeout'
            return result[0]
        def wait(predicate):
            deadline=time.monotonic()+12
            while time.monotonic()<deadline:
                if predicate():pump(.12);return
                pump()
            raise AssertionError('Timed out; visible='+str(js('document.body.innerText')))
        def has(text): return js(f'document.body.innerText.includes({json.dumps(text)})')
        def capture(label):
            pump(.15)
            window.view.grab().save(str(directory/'screenshots'/f'{len(checks)+1:02}-{label}.png'))
            checks.append(dict(step=label,text=js('document.body.innerText'),
                current=js("document.querySelectorAll('.class-point.current').length")))
            (directory/'ui-result.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
        def click(text):
            match=f"(b.querySelector('.choice > span')?.textContent??(b.matches('.choice')?b.querySelector('span')?.textContent:b.textContent)).trim()==={json.dumps(text)}&&!b.disabled"
            wait(lambda:js(f"!![...document.querySelectorAll('button')].find(b=>{match})"))
            assert js(f"(()=>{{let b=[...document.querySelectorAll('button')].find(b=>{match});b.click();return true}})()")
            pump(.2)
        def reply(text):
            js(f"(()=>{{let t=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,{json.dumps(text)});t.dispatchEvent(new Event('input',{{bubbles:true}}));}})()")
            pump(.1)
            # Native icon button has an accessible title.
            click('发送')
        try:
            wait(lambda:has('开课'));capture('home');click('开课');wait(lambda:has('开始上课'));capture('enter')
            click('开始上课');wait(lambda:has('第 1/3 步'));capture('first-step')
            click('继续');wait(lambda:has('甲先入队'));capture('guide-choice')
            click('甲');wait(lambda:has('甲先进入，因此先取出'));capture('guide-response')
            reply('没听懂，生活中队列是什么意思？');wait(lambda:has('回到刚才'));capture('interjection')
            click('回到刚才');wait(lambda:js("!!document.querySelector('.lesson-actions button')&&document.querySelector('.lesson-actions button').textContent.trim()==='继续'"));capture('back-same-step')
            click('继续');wait(lambda:has('第 3/3 步'));click('继续');wait(lambda:has('队列的取出顺序是什么'));capture('casual-one')
            click('先进入的先取出');wait(lambda:has('第二点'));capture('correct-next-live')
            click('继续');wait(lambda:has('甲先入队'));click('甲');click('继续');wait(lambda:has('队列的取出顺序是什么'))
            click('后进入的先取出');wait(lambda:has('这题没答对'));wait(lambda:has('甲先进入、乙后进入'));capture('wrong-second-question')
            click('甲');wait(lambda:has('开始模块检测'));capture('module-test-offer')
            click('开始模块检测');wait(lambda:has('固定检测'));capture('module-test-entry')
            click('下课');wait(lambda:has('查看全部计划'))
            # Report fixture uses actual mastery events and report builder; it
            # does not claim that fake UI test completed the whole curriculum.
            seq=100
            store.append_judgement(PLAN,Judgement('jmodule',seq,datetime.now(timezone.utc),'module_test','m','a',(('p0',True),('p1',True))))
            store.append_attempt_closed(PLAN,AttemptClosed('closed',seq+1,'m','a','module_test'))
            store.start_checking(PLAN)
            while not store.load_mastery(PLAN).check_complete():
                seq+=2
                pending=tuple((p,True) for p in ('p0','p1') if store.load_mastery(PLAN).point(p).level<3)
                store.append_judgement(PLAN,Judgement(f'j{seq}',seq,datetime.now(timezone.utc),'check','m',None,pending,'recall'))
            store.complete_plan(PLAN,today=day);build_report(store,PLAN,today=day)
            click('查看全部计划');click('全部');wait(lambda:has('真实后端分步联调'))
            assert js("(()=>{let c=[...document.querySelectorAll('.card')].find(c=>c.textContent.includes('真实后端分步联调'));let b=c.querySelector('button');b.click();return true})()")
            wait(lambda:has('学习报告'));click('学习报告');wait(lambda:has('现场讲的'));capture('report-live-point')
            assert has('第二点')
            assert all(c['current']==1 for c in checks if c['step'] in ('first-step','guide-choice','guide-response','back-same-step','correct-next-live'))
            assert not any(e.get('ok') is False for e in events if e.get('type')=='response'), events
        finally:
            (directory/'bridge-events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
            (directory/'fake-calls.json').write_text(json.dumps(gateway.calls),encoding='utf-8')
            window.close();pump(.2)


def test_prep26_real_backend_in_built_qt_interface(tmp_path):
    build(tmp_path)
    env=dict(os.environ,PYTHONPATH=str(REPO),PYTHONUTF8='1')
    result=subprocess.run([sys.executable,'-B',str(Path(__file__)),'--browser',str(tmp_path)],
        cwd=REPO,env=env,capture_output=True,text=True,encoding='utf-8',timeout=100)
    assert result.returncode==0,result.stdout+result.stderr
    checks=json.loads((tmp_path/'ui-result.json').read_text(encoding='utf-8'))
    assert len(checks)==13


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--browser');parser.add_argument('--artifacts');args=parser.parse_args()
    if args.artifacts:build(Path(args.artifacts));browser(Path(args.artifacts))
    else:browser(Path(args.browser))
