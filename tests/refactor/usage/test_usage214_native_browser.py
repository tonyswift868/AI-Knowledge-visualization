"""Real Usage React -> production QWebChannel -> production usage handlers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO = Path(__file__).resolve().parents[3]
NODE = Path('D:/MentorFlow/.local/node/node.exe')
DEPS = Path('D:/MentorFlow/.local/web-deps')
sys.path.insert(0, str(REPO))


def _build(root):
    web = REPO / 'refactor/learning/web'
    entry = Path(__file__).with_name('usage214_native_browser.tsx')
    (root / 'index.html').write_text(
        '<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div>'
        '<script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root / 'entry.tsx').write_text('import ' + json.dumps(entry.as_posix()), encoding='utf-8')
    (root / 'vite.config.mjs').write_text(
        'import base from ' + json.dumps((web / 'vite.config.mjs').as_uri()) +
        ';export default {...base,root:' + json.dumps(root.as_posix()) +
        ',build:{outDir:"dist",emptyOutDir:true}}', encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS=str(DEPS))
    result = subprocess.run(
        [str(NODE), str(DEPS / 'node_modules/vite/bin/vite.js'), 'build', '--config', str(root / 'vite.config.mjs')],
        env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


def test_usage214_native_react_bridge_handlers_and_paging(tmp_path):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from PySide6.QtCore import QUrl, Slot
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from refactor.learning.app import handlers
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.connections.providers import Usage
    from refactor.learning.connections.usage import UsageLog
    from tests.refactor.usage.helpers import catalog, row, units
    from tests.refactor.app.test_app_window_layer import _Window

    root = tmp_path / 'native'
    root.mkdir()
    _build(root)

    now = datetime.now(timezone.utc).replace(microsecond=0)
    prices = []
    for i in range(51):
        price = row()
        price.update(input_cache_hit_per_mtok='0', input_cache_miss_per_mtok='1.5',
                     output_per_mtok='0', search_per_request='0')
        price['model'] = f'page-{i:02d}'
        prices.append(price)
    log = UsageLog(tmp_path / 'isolated-current-account.sqlite3', catalog=catalog(*prices))
    log.record('test', 'page-00', 'connection_test', Usage(10, 0, 10, 0, 0, 0, {}),
               request_id='tiny-000015', at=now.isoformat())
    log.record('test', 'page-01', 'teacher', units(input=10, cached=0, output=0,
               raw={'_mentorflow': {'partial_usage': True}}),
               request_id='partial-known', at=(now - timedelta(seconds=1)).isoformat())
    log.record('test', 'page-02', 'teacher', None, request_id='unknown-cost',
               at=(now - timedelta(seconds=2)).isoformat(), error_code='MODEL_TIMEOUT')
    for i in range(3, 51):
        log.record('test', f'page-{i:02d}', 'teacher', units(input=10, cached=0, output=0),
                   request_id=f'page-call-{i:02d}', at=(now - timedelta(seconds=i)).isoformat())

    app = QApplication.instance() or QApplication([])
    profile = QWebEngineProfile()
    page = QWebEnginePage(profile)

    class Offline(QWebEngineUrlRequestInterceptor):
        blocked = []
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http', 'https'):
                self.blocked.append(info.requestUrl().toString())
                info.block(True)

    blocker = Offline(page)
    profile.setUrlRequestInterceptor(blocker)
    view = QWebEngineView()
    view.setPage(page)
    gateway = SimpleNamespace(usage_log=log, get_configuration=lambda: None)
    services = SimpleNamespace(account_id='isolated-current-account', gateway=gateway,
                               store=SimpleNamespace(list_plans=lambda: []))
    window = _Window(services)

    class TracedBridge(AppBridge):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.requests = []
            self.responses = []
            self.event.connect(self._trace_response)

        @Slot(str)
        def call(self, text):
            try:
                request = json.loads(text)
                self.requests.append(request)
            except (TypeError, ValueError):
                pass
            super().call(text)

        @Slot(str)
        def _trace_response(self, text):
            try:
                message = json.loads(text)
                if message.get('type') == 'response':
                    self.responses.append(message)
            except (TypeError, ValueError):
                pass

    bridge = TracedBridge(page)
    handlers.register(bridge, window)
    # No preset is configured in the isolated fake gateway, so the production
    # balance handler returns without making a vendor request.
    channel = QWebChannel(page)
    channel.registerObject('mf', bridge)
    page.setWebChannel(channel)
    view.resize(1200, 1000)
    view.show()
    view.setUrl(QUrl.fromLocalFile(str(root / 'dist/index.html')))

    def js(script, timeout=4):
        output = []
        page.runJavaScript(f'JSON.stringify({script})', output.append)
        until = time.monotonic() + timeout
        while not output and time.monotonic() < until:
            app.processEvents()
            time.sleep(.005)
        assert output, f'JavaScript evaluation timed out: {script}'
        return json.loads(output[0])

    def wait_for(script, timeout=8):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            app.processEvents()
            # Qt WebEngine may need a full event-loop second to answer the
            # first probe while the other xdist workers are browser-testing.
            if js(script, timeout=1):
                return
            time.sleep(.01)
        raise AssertionError(f'browser condition timed out: {script}; body={js("document.body.innerText")!r}')

    try:
        wait_for("window.usage214Result?.mounted === true")
        wait_for("document.body.innerText.includes('本应用估算费用') && document.body.innerText.includes('调用')")
        details_hidden = js("!document.body.innerText.includes('逐笔费用') && !document.querySelector('[aria-label=\\\"用量详情\\\"]')")
        assert details_hidden is True
        js("(() => { Array.from(document.querySelectorAll('button')).find(button => button.innerText.includes('查看详情')).click(); return true })()")
        wait_for("document.body.innerText.includes('已显示 50 / 51 次调用')")
        detail_state = js("""(() => {
          const section = document.querySelector('[aria-label="用量详情"]');
          const table = [...section.querySelectorAll('table')].at(-1);
          const rows = [...table.querySelectorAll('tbody tr')];
          return {rows: rows.length, text: rows.map(row => row.innerText),
            models: rows.map(row => row.querySelectorAll('td')[2].innerText.split('·').at(-1).trim())};
        })()""")
        assert detail_state['rows'] == 50
        first_text = detail_state['text']
        assert first_text[0].find('page-00') >= 0
        assert any('¥0.000015' in text for text in first_text)
        assert any('用量未完整返回' in text for text in first_text)
        assert any('仅返回部分用量' in text for text in first_text)
        js("(() => { const section = [...document.querySelectorAll('section')].find(item => item.getAttribute('aria-label') === '用量详情'); [...section.querySelectorAll('button')].find(button => button.innerText.includes('加载更多')).click(); return true })()")
        wait_for("document.body.innerText.includes('已显示 51 / 51 次调用')")
        models = js("""(() => {
          const section = document.querySelector('[aria-label="用量详情"]');
          const table = [...section.querySelectorAll('table')].at(-1);
          return [...table.querySelectorAll('tbody tr')]
            .map(row => row.querySelectorAll('td')[2].innerText.split('·').at(-1).trim());
        })()""")
        assert len(models) == 51 and len(set(models)) == 51
        assert models[0] == 'page-00' and models[-1] == 'page-50'
        text = js('document.body.innerText')
        assert '¥21.38' not in text and '今天花了' not in text
        methods = [request['method'] for request in bridge.requests]
        assert 'usage.overview' in methods and 'usage.summary' in methods and 'usage.details' in methods
        calls = [request for request in bridge.requests if request['method'] == 'usage.details']
        assert len(calls) == 2, calls
        first, second = calls
        assert first['params'].get('offset') == 0 and 'end' not in first['params'], first
        assert second['params'].get('offset') == 50 and second['params'].get('limit') == 50, second
        first_result = next(message['result'] for message in bridge.responses
                            if message.get('id') == first['id'] and message.get('ok'))
        assert second['params'].get('end') == first_result['end']
        assert not blocker.blocked, blocker.blocked
    finally:
        view.close()
        bridge.close()
        app.processEvents()
