"""Real RAG React page in an isolated QtWebEngine process with a local bridge fixture."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO = Path(__file__).resolve().parents[3]
OUT = Path('D:/MentorFlow/.local/test-tmp/ragui155/browser')
NODE = 'D:/MentorFlow/.local/node/node.exe'
WEB = REPO / 'refactor/learning/web'
sys.path.insert(0, str(REPO))


def build155(root):
    root.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name('browser155.tsx')
    (root/'entry.tsx').write_text('import '+json.dumps(entry.as_posix()), encoding='utf-8')
    (root/'index.html').write_text('<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div><script type="module" src="/entry.tsx"></script>', encoding='utf-8')
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((WEB/'vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',build:{outDir:"dist",emptyOutDir:true}}', encoding='utf-8')
    env = dict(os.environ, MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps')
    p = subprocess.run([NODE,'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js','build','--config',str(root/'vite.config.mjs')], env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert p.returncode == 0, p.stdout+p.stderr


def host155(root):
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
    ingest_calls=0
    preview_path=os.environ.get('MF_RAG_PREVIEW_JSON')
    if preview_path and Path(preview_path).is_file():
        preview=json.loads(Path(preview_path).read_text(encoding='utf-8'))
        preview_session=preview.get('session') or preview
        if isinstance(preview_session,dict) and isinstance(preview_session.get('messages'),list):
            doc_map={}
            live_docs={str(d.get('id')):d for d in (preview.get('sources',{}).get('documents',[]) or []) if isinstance(d,dict)}
            for message in preview_session['messages']:
                for passage in message.get('passages',[]) or []:
                    doc_id=str(passage.get('document_id') or '')
                    if doc_id:
                        title=(live_docs.get(doc_id) or {}).get('title') or passage.get('title') or doc_id
                        passage.setdefault('title',title)
                        doc_map[doc_id]=dict(id=doc_id,title=title,source='真实问答测试资料',plan_ids=[],status='ready',total_chunks=1,uploaded_chunks=1)
            for doc_id in preview_session.get('document_ids',[]) or []:
                doc_map.setdefault(str(doc_id),dict(id=str(doc_id),title=str(doc_id),source='真实问答测试资料',plan_ids=[],status='ready',total_chunks=1,uploaded_chunks=1))
            docs=list(doc_map.values())
            plans=[]
            preview_session.setdefault('id','live-session-155')
            preview_session.setdefault('title','资料问答 · 真实结果')
            preview_session.setdefault('document_ids',list(doc_map))
            preview_session.setdefault('updated_at','2026-10-16T00:00:00')
            preview_session.setdefault('count',len(preview_session['messages']))
            sessions.append(preview_session)
            by_id[preview_session['id']]=preview_session
    def session(doc_ids):
        row=dict(id='session-155',title='计算机基础问答',document_ids=list(doc_ids),updated_at='2026-10-16T09:00:00',count=0,messages=[])
        sessions.insert(0,row);by_id[row['id']]=row;return row
    bridge.register('app.bootstrap',lambda p,c:dict(version='test',account=dict(name='本地测试',role_label='学习者'),settings=dict(theme='sakura',font_scale=1,reduce_motion=True),options={},notice=None,ready=True))
    bridge.register('settings.get',lambda p,c:dict(settings=dict(theme='sakura',font_scale=1,reduce_motion=True),options={}))
    bridge.register('sync.status',lambda p,c:dict(state='synced',at=None,message='已同步'))
    bridge.register('models.status',lambda p,c:dict(state='unknown',short='模型待验证'))
    bridge.register('update.status',lambda p,c:dict(available=False,busy=False,state='UP_TO_DATE',version='test'))
    bridge.register('update.automatic',lambda p,c:dict(available=False,busy=False,state='UP_TO_DATE',version='test'))
    def trace(name,p):
        with (Path(root)/'bridge-trace155.txt').open('a',encoding='utf-8') as stream: stream.write(name+' '+json.dumps(p,ensure_ascii=False)+'\n')
    def sources(p,c): return dict(plans=plans,documents=docs,sessions=[{k:v for k,v in s.items() if k!='messages'} for s in sessions])
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
                    while not (Path(root)/'release-ingest155').exists():
                        if c.cancel.wait(.02): return {'documents':docs}
            failed=d['id']=='upload-155' and ingest_calls==1
            d['status']='failed' if failed else 'ready'
            bridge.push('rag.progress',dict(document_id=d['id'],status=d['status'],uploaded_chunks=d['uploaded_chunks'],total_chunks=d['total_chunks'],error='本地假服务器模拟的失败' if failed else None,completed=done,total=total))
        return dict(documents=docs)
    bridge.register('rag.ingest',ingest)
    upload=Path(root)/'upload155.txt';upload.write_text('本地上传材料：向量检索按语义相似度寻找相关内容。',encoding='utf-8')
    bridge.register('rag.pick_files',lambda p,c:dict(paths=[str(upload)]))
    def import_files(p,c):
        new=dict(id='upload-155',title='自选补充材料.txt',source='本地上传',plan_ids=[],status='not_uploaded',total_chunks=1,uploaded_chunks=0)
        docs.append(new);return dict(documents=[new])
    bridge.register('rag.import_files',import_files)
    def create(p,c): trace('create',p); return dict(session=session(p.get('document_ids',[])))
    bridge.register('rag.create',create)
    bridge.register('rag.delete',lambda p,c:(sessions.__setitem__(slice(None),[s for s in sessions if s['id']!=p.get('session_id')]) or by_id.pop(p.get('session_id'),None) or {'ok':True}))
    def send(p,c):
        trace('send',p)
        fail_marker=Path(root)/'fail-next-send155'
        if fail_marker.exists():
            fail_marker.unlink()
            error=ModelError('MODEL_CONNECTION_FAILED','本地测试模拟模型连接失败。')
            error.model_fault=dict(id='fault-155',purpose='rag_qa',plan_id='',category='network',code=error.code,
                                   message='连不上模型服务，请检查网络',short='模型连接失败',actions=[dict(id='retry',label='重试')])
            raise error
        s=by_id[p['session_id']];s['count']+=2
        user=dict(id=f'user-{s["count"]}',role='user',content=p.get('text',''),passages=[])
        answer='资料显示，向量检索会根据语义相似度查找段落。[1]\n\n资料外补充：实际系统常会结合其他检索方法。'
        preview=os.environ.get('MF_RAG_PREVIEW_JSON')
        if preview and Path(preview).is_file():
            data=json.loads(Path(preview).read_text(encoding='utf-8'))
            answer=data.get('answer') or data.get('content') or data.get('text') or answer
        c.stream('资料显示，向量检索会根据语义')
        while not (Path(root)/'release-stream155').exists():
            if c.cancel.wait(.02): return {'session':s}
        c.stream('相似度查找段落。[1]\n\n资料外补充：实际系统常会结合其他检索方法。')
        msg=dict(id=f'assistant-{s["count"]}',role='assistant',content=answer,passages=[dict(number=1,document_id='doc-a',seq=2,text='向量检索会把文字转换成向量，再按语义相似度寻找相关段落。',page=4,section='向量检索',title='计算机基础.md')])
        s['messages'] += [user,msg]
        bridge.push('model_status',dict(state='ok',short='模型连接正常'))
        return dict(session=s)
    bridge.register('rag.send',send)
    view.resize(1000,780);view.show();view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    timer=QTimer();timer.timeout.connect(lambda:app.quit() if (root/'done155').exists() else None);timer.start(100)
    try: app.exec()
    finally: bridge.close()


def drive155(root):
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1',PATH='D:/MentorFlow/.local/node;'+os.environ.get('PATH',''))
    with (root/'qt155.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(root)],env=env,stdout=log,stderr=log)
        try:
            p=subprocess.run([NODE,str(Path(__file__).with_name('browser155.mjs')),str(root)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
            assert p.returncode==0,p.stdout+p.stderr
        finally:(root/'done155').touch();child.wait(timeout=15)
    return json.loads((root/'result155.json').read_text(encoding='utf-8'))


def capture_live155(root, preview_json):
    """Screenshot-only path: reuses dist, displays supplied real output, runs no assertions."""
    if not (root/'dist/index.html').is_file():
        raise FileNotFoundError('fixture dist is missing; run the related browser test first')
    for name in ('done155','release-ingest155','release-stream155'):
        try:(root/name).unlink()
        except FileNotFoundError:pass
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',MF_RAG_PREVIEW_JSON=str(Path(preview_json).resolve()),PYTHONDONTWRITEBYTECODE='1',PATH='D:/MentorFlow/.local/node;'+os.environ.get('PATH',''))
    with (root/'qt-preview155.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(root)],env=env,stdout=log,stderr=log)
        try:
            p=subprocess.run([NODE,str(Path(__file__).with_name('live_preview155.mjs')),str(root)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
            if p.returncode:raise RuntimeError(p.stdout+p.stderr)
            return json.loads(p.stdout.strip().splitlines()[-1])
        finally:(root/'done155').touch();child.wait(timeout=15)


def test_rag155_real_react_qt_bridge_three_selection_paths_stream_citations_sessions_and_themes(tmp_path):
    out = tmp_path / 'browser155'
    out.mkdir(parents=True,exist_ok=True)
    for name in ('done155','release-ingest155','release-stream155','result155.json','bridge-trace155.txt','sakura155.png','aurora155.png'):
        try:(out/name).unlink()
        except FileNotFoundError:pass
    build155(out)
    result=drive155(out)
    assert result['ok'],result


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--host':host155(Path(sys.argv[2]))
    elif len(sys.argv)>1 and sys.argv[1]=='--capture':print(json.dumps(capture_live155(OUT,sys.argv[2]),ensure_ascii=False))
