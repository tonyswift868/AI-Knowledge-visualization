"""Real RAG React page in an isolated QtWebEngine process with a local bridge fixture."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
NODE = 'D:/MentorFlow/.local/node/node.exe'
WEB = REPO / 'refactor/learning/web'
sys.path.insert(0, str(REPO))


def build181(root):
    root.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name('browser181.tsx')
    (root/'entry.tsx').write_text('import '+json.dumps(entry.as_posix()), encoding='utf-8')
    (root/'index.html').write_text('<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((WEB/'vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',build:{outDir:"dist",emptyOutDir:true}}', encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps')
    p = subprocess.run([NODE,'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js','build','--config',str(root/'vite.config.mjs')], env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert p.returncode == 0, p.stdout+p.stderr


def host181(root):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.connections.errors import ModelError
    from tests.refactor.simulation.engine import ensure_offscreen_fonts

    app=QApplication([]);ensure_offscreen_fonts()
    view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ('http','https'): info.block(True)
    blocker=Offline(page);profile.setUrlRequestInterceptor(blocker);view.setPage(page)
    bridge=AppBridge(page);channel=QWebChannel(page);channel.registerObject('mf',bridge);page.setWebChannel(channel)
    docs=[
      dict(id='doc-a',title='计算机基础.md',source='上传资料',plan_ids=['plan-a'],status='ready',total_chunks=2,uploaded_chunks=2),
      dict(id='doc-b',title='网络概念.md',source='上传资料',plan_ids=['plan-a'],status='not_uploaded',total_chunks=3,uploaded_chunks=0),
      dict(id='doc-c',title='机器学习.md',source='上传资料',plan_ids=['plan-b'],status='ready',total_chunks=1,uploaded_chunks=1),
    ]
    plans=[dict(id='plan-a',title='计算机入门',document_ids=['doc-a','doc-b']),dict(id='plan-b',title='人工智能基础',document_ids=['doc-c'])]
    sessions=[];by_id={}
    sources_calls=0; sources_active=0; sources_max_active=0
    account_recovered=False
    ingest_calls=0
    def session(doc_ids):
        row=dict(id='session-181',title='计算机基础问答',document_ids=list(doc_ids),updated_at='2026-10-16T09:00:00',count=0,messages=[])
        sessions.insert(0,row);by_id[row['id']]=row;return row
    bridge.register('app.bootstrap',lambda p,c:dict(version='test',account=dict(name='本地测试',role_label='学习者',offline=True),settings=dict(theme='sakura',font_scale=1,reduce_motion=True),options={},notice=dict(message='当前离线，已使用此设备上次验证的账号进入；连接恢复后会自动核对并同步。',show_verify=False),ready=True))
    bridge.register('settings.get',lambda p,c:dict(settings=dict(theme='sakura',font_scale=1,reduce_motion=True),options={}))
    bridge.register('voice.status',lambda p,c:dict(state='not_downloaded',installed=False,invalid=False,
      model_name='本机语音模型',size_bytes=1,download_url='',job_id=None,downloaded_bytes=0,total_bytes=0,cancelling=False))
    bridge.register('plans.list',lambda p,c:dict(plans=[],study_day='181-fixture'))
    bridge.register('sync.status',lambda p,c:dict(state='synced',at=None,message='已同步'))
    bridge.register('models.status',lambda p,c:dict(state='unknown',short='模型待验证'))
    bridge.register('update.status',lambda p,c:dict(available=False,busy=False,state='UP_TO_DATE',version='test'))
    bridge.register('update.automatic',lambda p,c:dict(available=False,busy=False,state='UP_TO_DATE',version='test'))
    def trace(name,p):
        with (Path(root)/'bridge-trace181.txt').open('a',encoding='utf-8') as stream: stream.write(name+' '+json.dumps(p,ensure_ascii=False)+'\n')
    def sources(p,c):
        nonlocal sources_calls,sources_active,sources_max_active,account_recovered
        sources_calls+=1;sources_active+=1;sources_max_active=max(sources_max_active,sources_active)
        import time;time.sleep(.8)
        recovered=(Path(root)/'recover-marker181').exists()
        status={'state':'ready','message':''} if recovered else {'state':'unavailable','message':'资料服务离线，正在后台重试。'}
        if recovered and not account_recovered:
            account_recovered=True
            bridge.push('account',dict(name='本地测试',role_label='学习者',offline=False))
            bridge.push('session_notice',None)
        sources_active-=1
        (Path(root)/'sources-count181.json').write_text(json.dumps(dict(calls=sources_calls,active=sources_active,max_active=sources_max_active)),encoding='utf-8')
        return dict(plans=plans,documents=docs,sessions=[{k:v for k,v in s.items() if k!='messages'} for s in sessions],server_status=status)
    bridge.register('rag.sources',sources)
    def get_session(p,c): trace('get',p); return dict(session=by_id.get(p.get('session_id')))
    bridge.register('rag.get',get_session)
    def ingest(p,c):
        nonlocal ingest_calls
        ingest_calls += 1
        ids=p.get('document_ids',[])
        total=sum(d['total_chunks'] for d in docs if d['id'] in ids)
        done=0
        for d in docs:
            if d['id'] not in ids: continue
            d['status']='uploading';d['uploaded_chunks']=0
            bridge.push('rag.progress',dict(document_id=d['id'],status='uploading',uploaded_chunks=0,total_chunks=d['total_chunks']))
            for n in range(1,d['total_chunks']+1):
                d['uploaded_chunks']=n;done+=1
                bridge.push('rag.progress',dict(document_id=d['id'],status='uploading',uploaded_chunks=n,total_chunks=d['total_chunks'],completed=done,total=total))
                if done == 1:
                    while not (Path(root)/'release-ingest181').exists():
                        if c.cancel.wait(.02): return {'documents':docs}
            failed=d['id']=='upload-181' and ingest_calls==1
            d['status']='failed' if failed else 'ready'
            bridge.push('rag.progress',dict(document_id=d['id'],status=d['status'],uploaded_chunks=d['uploaded_chunks'],total_chunks=d['total_chunks'],error='本地假服务器模拟的失败' if failed else None,completed=done,total=total))
        return dict(documents=docs)
    bridge.register('rag.ingest',ingest)
    upload=Path(root)/'upload181.txt';upload.write_text('本地上传材料：向量检索按语义相似度寻找相关内容。',encoding='utf-8')
    bridge.register('rag.pick_files',lambda p,c:dict(paths=[str(upload)]))
    def import_files(p,c):
        new=dict(id='upload-181',title='自选补充材料.txt',source='本地上传',plan_ids=[],status='not_uploaded',total_chunks=1,uploaded_chunks=0)
        docs.append(new);return dict(documents=[new])
    bridge.register('rag.import_files',import_files)
    def create(p,c): trace('create',p); return dict(session=session(p.get('document_ids',[])))
    bridge.register('rag.create',create)
    def update_documents(p,c):
        row=by_id[p['session_id']]
        row['document_ids']=list(p.get('document_ids',[]))
        trace('update_documents',p)
        return dict(session=row)
    bridge.register('rag.update_documents',update_documents)
    bridge.register('rag.delete',lambda p,c:(sessions.__setitem__(slice(None),[s for s in sessions if s['id']!=p.get('session_id')]) or by_id.pop(p.get('session_id'),None) or {'ok':True}))
    def send(p,c):
        trace('send',p)
        key_marker=Path(root)/'key-next-send181'
        if key_marker.exists():
            key_marker.unlink()
            error=ModelError('MODEL_KEY_INVALID','本地测试模拟 API Key 无效。')
            error.model_fault=dict(id='fault-key-181',purpose='rag_qa',plan_id='',category='key',code=error.code,
                                   message='API Key 无效，请到设置里检查',short='模型 Key 无效',actions=[dict(id='settings',label='去设置')])
            raise error
        fail_marker=Path(root)/'fail-next-send181'
        if fail_marker.exists():
            fail_marker.unlink()
            error=ModelError('MODEL_CONNECTION_FAILED','本地测试模拟模型连接失败。')
            error.model_fault=dict(id='fault-181',purpose='rag_qa',plan_id='',category='network',code=error.code,
                                   message='连不上模型服务，请检查网络',short='模型连接失败',actions=[dict(id='retry',label='重试')])
            raise error
        s=by_id[p['session_id']];s['count']+=2
        user=dict(id=f'user-{s["count"]}',role='user',content=p.get('text',''),passages=[])
        answer='资料显示，向量检索会根据语义相似度查找段落。[1]\n\n资料外补充：实际系统常会结合其他检索方法。'
        c.stream('资料显示，向量检索会根据语义')
        while not (Path(root)/'release-stream181').exists():
            if c.cancel.wait(.02): return {'session':s}
        c.stream('相似度查找段落。[1]\n\n资料外补充：实际系统常会结合其他检索方法。')
        msg=dict(id=f'assistant-{s["count"]}',role='assistant',content=answer,passages=[dict(number=1,document_id='doc-a',seq=2,text='向量检索会把文字转换成向量，再按语义相似度寻找相关段落。',page=4,section='向量检索',title='计算机基础.md')])
        s['messages'] += [user,msg]
        bridge.push('model_status',dict(state='ok',short='模型连接正常'))
        return dict(session=s)
    bridge.register('rag.send',send)
    view.resize(1000,780);view.show();view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    timer=QTimer();timer.timeout.connect(lambda:app.quit() if (root/'done181').exists() else None);timer.start(100)
    try: app.exec()
    finally: bridge.close()


def drive181(root):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ,TEMP=str(root),TMP=str(root),TMPDIR=str(root),QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1',PATH='D:/MentorFlow/.local/node;'+os.environ.get('PATH',''))
    with (root/'qt181.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(root)],env=env,stdout=log,stderr=log)
        try:
            p=subprocess.run([NODE,str(Path(__file__).with_name('browser181.mjs')),str(root)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert p.returncode==0,p.stdout+p.stderr
        finally:(root/'done181').touch();child.wait(timeout=15)
    return json.loads((root/'result181.json').read_text(encoding='utf-8'))


def test_rag181_browser_offline_recovery_and_manual_question_retry(tmp_path):
    OUT = tmp_path / 'rag'
    OUT.mkdir(parents=True,exist_ok=True)
    for name in ('done181','recover-marker181','fail-next-send181','key-next-send181','release-stream181','result181.json','bridge-trace181.txt','sources-count181.json'):
        try:(OUT/name).unlink()
        except FileNotFoundError:pass
    (OUT.parent/'screenshots').mkdir(parents=True,exist_ok=True)
    build181(OUT)
    result=drive181(OUT)
    assert result['ok'],result
    assert (OUT.parent/'screenshots/offline-rag181.png').is_file()
    assert (OUT.parent/'screenshots/offline-account181.png').is_file()
    assert (OUT.parent/'screenshots/offline-recovered181.png').is_file()
    assert (OUT.parent/'screenshots/question-retry181.png').is_file()


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--host':host181(Path(sys.argv[2]))
