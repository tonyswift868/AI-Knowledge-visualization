"""Opt-in built UI + real Qt bridge + real Classroom, deliberately blocked fake prep."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest


def browser(root):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
    from datetime import date, timedelta
    from PySide6.QtWidgets import QApplication
    from refactor.learning.app import window as wm
    from refactor.learning.app.services import AppServices
    from refactor.learning.app.classroom import Classroom
    from refactor.learning.course.ledger import PlanWindow
    from tests.refactor.prep_retest.test_prep26_backend_webengine import FakeGateway
    app = QApplication([])
    root.mkdir(parents=True, exist_ok=True)
    (root/'screenshots').mkdir(exist_ok=True)
    services = AppServices(account_id='prep32_ui', data_root=root/f'ui-account-{time.time_ns()}', session_provider=lambda: None,
                           api_base_url='https://example.invalid', ca_bundle=None)
    gate = threading.Event()
    class SlowGateway(FakeGateway):
        def complete(self, purpose, messages, **kwargs):
            if purpose == 'lesson_prep':
                assert gate.wait(15), 'test did not release model'
            return super().complete(purpose, messages, **kwargs)
    services._gateway = SlowGateway()
    services.settings.save(dict(detail='brief', reduce_motion=True))
    store, plan, day = services.store, 'c'*32, date.today()
    store.create_plan(plan, goal='非阻塞课堂', window=PlanWindow(day, day+timedelta(days=1), 200))
    store.add_sources(plan, [dict(id='s', kind='url', title='合成资料', url='https://example.invalid',
                                  chunks=[dict(chunk_id='c', text='队列先入先出。')])])
    store.save_outline(plan, [dict(id='st', title='顺序')], [dict(id='m', stage_id='st', title='队列')])
    store.save_generated_module(plan, 'm', [dict(id=f'p{i}', title=f'点{i}', summary='先入先出',
        minutes=15, chunk_ids=['c']) for i in range(2)], check_minutes=8, today=day)
    store.start_plan(plan, today=day)
    records, wire_events = [], []
    with patch.object(wm, 'WEB_ROOT', root/'web-dist'), \
         patch.object(Classroom, '_prepare_later', lambda *a, **k: None), \
         patch.object(Classroom, '_schedule_idle_prep', lambda *a, **k: None), \
         patch.object(Classroom, '_prefetch_banks', lambda *a, **k: None), \
         patch.object(Classroom, '_profile_later', lambda *a, **k: None):
        window = wm.LearningWindow('合成身份', 'LEARNER', 'example.invalid',
                                   account_id=services.account_id, data_root=services.data_root)
        window.services = services
        window.resize(1440, 900); window.show()
        window.bridge.event.connect(lambda raw: wire_events.append(json.loads(raw)))
        def pump(seconds=.02):
            until = time.monotonic()+seconds
            while time.monotonic() < until:
                app.processEvents(); time.sleep(.002)
        def js(code):
            values = []; window.page.runJavaScript(code, values.append)
            deadline = time.monotonic()+5
            while not values and time.monotonic() < deadline: pump()
            assert values, 'JS timeout'
            return values[0]
        def has(text): return js(f'document.body.innerText.includes({json.dumps(text)})')
        def wait(predicate):
            deadline = time.monotonic()+10
            while not predicate():
                assert time.monotonic() < deadline, js('document.body.innerText')
                pump()
        def click(label):
            expression = f"[...document.querySelectorAll('button')].find(b=>!b.disabled&&(b.querySelector('.choice>span')?.textContent??b.textContent).trim()==={json.dumps(label)})"
            wait(lambda: js(f'!!({expression})'))
            js(f'({expression}).click()'); pump(.05)
        def capture(label):
            pump(.2)  # allow Chromium paint after its DOM state update
            window.view.grab().save(str(root/'screenshots'/f'{len(records)+1:02}-{label}.png'))
            records.append(dict(step=label, text=js('document.body.innerText')))
        try:
            wait(lambda: has('开课')); click('开课'); wait(lambda: has('开始上课'))
            click('开始上课'); wait(lambda: has('老师正在准备这一点'))
            capture('preparing-input-enabled')
            pump(1.1); assert has('已等 1 秒') or has('已等 2 秒')
            js("(()=>{let t=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'午饭聊一句');t.dispatchEvent(new Event('input',{bubbles:true}));})()")
            pump(.05); click('发送'); wait(lambda: has('这次插话'))
            assert has('老师正在准备这一点'); capture('interruption-while-preparing')
            js("(()=>{let t=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'尚未发送的插话');t.dispatchEvent(new Event('input',{bubbles:true}));})()")
            pump(.05)
            gate.set(); wait(lambda: has('第 1/3 步')); capture('automatic-first-step')
            assert not has('老师正在准备这一点')
            assert js("document.querySelector('textarea').value") == '尚未发送的插话'
            js("(()=>{let t=document.querySelector('textarea');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(t,'');t.dispatchEvent(new Event('input',{bubbles:true}));})()")
            click('继续'); wait(lambda: has('甲先入队')); click('甲')
            click('继续'); wait(lambda: has('第 3/3 步')); click('继续')
            wait(lambda: has('队列的取出顺序是什么'))
            gate.clear(); click('先进入的先取出')
            wait(lambda: has('老师正在准备这一点')); capture('verdict-and-next-preparing')
            assert has('答对了')
            gate.set(); wait(lambda: has('第 1/3 步')); capture('automatic-next-point')
            assert len([e for e in wire_events if e.get('name') == 'classroom.lesson_ready']) == 2
            (root/'ui-result.json').write_text(json.dumps(dict(checks=records,
                events=[e for e in wire_events if e.get('name') == 'classroom.lesson_ready']),
                ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            gate.set(); window.close(); pump(.2)
            for thread in threading.enumerate():
                if thread.name == 'classroom-needed-lesson': thread.join(3)
            services.close()


@pytest.mark.skipif(os.environ.get('MF32_WEBENGINE') != '1', reason='built Qt UI probe opt-in')
def test_prep32_real_qt_bridge_wait_ui_and_auto_event():
    root = Path(os.environ['MF32_ARTIFACTS'])
    result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve()), str(root)],
                            capture_output=True, text=True, encoding='utf-8', timeout=80)
    (root/'ui.log').write_text(result.stdout+result.stderr, encoding='utf-8')
    assert result.returncode == 0, result.stdout+result.stderr


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    browser(Path(sys.argv[1]))
