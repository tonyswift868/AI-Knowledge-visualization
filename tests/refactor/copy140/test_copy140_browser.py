"""Real Chat React + QWebEngine + production QWebChannel, with local chat replies."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
NODE = 'D:/MentorFlow/.local/node/node.exe'
MARKDOWN = '**向量检索**\n\n- 保留 `a < b`\n- [资料](https://example.invalid)\n\n```python\nprint("原文")\n```\n'


def build140(root):
    root.mkdir(parents=True, exist_ok=True)
    web = REPO / 'refactor/learning/web'
    entry = Path(__file__).with_name('browser140.tsx')
    (root/'entry.tsx').write_text('import '+json.dumps(entry.as_posix()), encoding='utf-8')
    (root/'index.html').write_text('<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((web/'vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',build:{outDir:"dist",emptyOutDir:true}}', encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps')
    result = subprocess.run([NODE, 'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js', 'build', '--config', str(root/'vite.config.mjs')], env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr


def host140(root):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge, BridgeError
    from tests.refactor.simulation.engine import ensure_offscreen_fonts
    app=QApplication([]);ensure_offscreen_fonts()
    view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            if info.requestUrl().scheme() in ('http','https'): info.block(True)
    blocker=Offline(page);profile.setUrlRequestInterceptor(blocker);view.setPage(page)
    # Same file:// clipboard permissions as app/window.py: no clipboard override.
    settings=page.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,False)
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,False)
    bridge=AppBridge(page);channel=QWebChannel(page);channel.registerObject('mf',bridge);page.setWebChannel(channel)
    session=dict(id='copy140',title='教师闲聊 · 复制验证',updated_at='2026-10-15T10:00:00',count=2,
        messages=[dict(role='user',content='请解释向量检索。',seq=1),dict(role='assistant',content=MARKDOWN,seq=2)])
    bridge.register('chat.list',lambda p,c:dict(sessions=[{k:v for k,v in session.items() if k!='messages'}]))
    bridge.register('chat.get',lambda p,c:dict(session=session))
    def send(params,context):
        context.stream('这是**尚未完成**的回复。')
        while not (root/'finish').exists():
            if context.cancel.wait(.01): raise BridgeError('CANCELLED','已取消。')
        session['messages'].extend([dict(role='user',content=params['text'],seq=3),dict(role='assistant',content='流式完成后的 **Markdown**。',seq=4)])
        session['count']=4
        return dict(session=session)
    bridge.register('chat.send',send)
    production_copy=bridge._handlers['app.copy_text']
    def copy(params,context):
        if (root/'reject').exists(): raise BridgeError('CLIPBOARD_UNAVAILABLE','测试剪贴板不可用。')
        result=production_copy.function(params,context)
        # Only read after writing our synthetic test text; never read prior user clipboard.
        with (root/'clipboard140.jsonl').open('a',encoding='utf-8') as out:
            out.write(json.dumps(dict(text=params['text'],actual=QGuiApplication.clipboard().text()),ensure_ascii=False)+'\n')
        return result
    bridge._handlers.pop('app.copy_text');bridge.register('app.copy_text',copy,ui=True)
    view.resize(1100,850);view.show();view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    timer=QTimer();timer.timeout.connect(lambda:app.quit() if (root/'done').exists() else None);timer.start(100)
    try: app.exec()
    finally: bridge.close()


def drive140(root):
    with socket.socket() as s: s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ, QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',
        QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*', MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1')
    with (root/'qt140.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(root)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([NODE,str(Path(__file__).with_name('browser140.mjs')),str(root)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
            assert result.returncode==0,result.stdout+result.stderr
        finally: (root/'done').touch();child.wait(timeout=15)
    return json.loads((root/'result140.json').read_text(encoding='utf-8'))


def test_copy140_completed_markdown_native_clipboard_fallback_feedback_and_themes(tmp_path):
    build140(tmp_path)
    result=drive140(tmp_path)
    assert result['ok'],result
    writes=[json.loads(line) for line in (tmp_path/'clipboard140.jsonl').read_text(encoding='utf-8').splitlines()]
    assert writes and all(w['text']==w['actual']==MARKDOWN for w in writes)


if __name__=='__main__':host140(Path(sys.argv[2]))
