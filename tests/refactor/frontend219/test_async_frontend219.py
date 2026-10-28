"""Actual App, Rag and PlanDetail components over a controlled offline Qt bridge."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

REPO=Path(__file__).resolve().parents[3]
WEB=REPO/'refactor/learning/web'
NODE=Path('D:/MentorFlow/.local/node/node.exe')
DEPS=Path('D:/MentorFlow/.local/web-deps')
sys.path.insert(0,str(REPO))

def build(root):
    root.mkdir(parents=True,exist_ok=True)
    here=Path(__file__).resolve().parent
    (root/'index.html').write_text('<script src="qrc:///qtwebchannel/qwebchannel.js"></script><div id="root"></div><script type="module" src="/entry.tsx"></script>',encoding='utf-8')
    (root/'entry.tsx').write_text('import '+json.dumps((here/'async_frontend219.tsx').as_posix()),encoding='utf-8')
    (root/'vite.config.mjs').write_text('import base from '+json.dumps((WEB/'vite.config.mjs').as_uri())+';export default {...base,root:'+json.dumps(root.as_posix())+',build:{outDir:"dist",emptyOutDir:true}}',encoding='utf-8')
    env=dict(os.environ,MENTORFLOW_WEB_DEPS=str(DEPS),TEMP=str(root),TMP=str(root))
    p=subprocess.run([str(NODE),str(DEPS/'node_modules/vite/bin/vite.js'),'build','--config',str(root/'vite.config.mjs')],env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
    assert p.returncode==0,p.stdout+p.stderr

def host(root):
    from PySide6.QtCore import QTimer,QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebChannel import QWebChannel
    from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineUrlRequestInterceptor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from refactor.learning.app.bridge import AppBridge,BridgeError
    from tests.refactor.simulation.engine import ensure_offscreen_fonts
    import threading,time
    app=QApplication([]);ensure_offscreen_fonts();view=QWebEngineView();profile=QWebEngineProfile(view);page=QWebEnginePage(profile,view)
    class Offline(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self,info):
            if info.requestUrl().scheme() in ('http','https'):info.block(True)
    blocker=Offline(page);profile.setUrlRequestInterceptor(blocker);view.setPage(page)
    bridge=AppBridge(page);channel=QWebChannel(page);channel.registerObject('mf',bridge);page.setWebChannel(channel)
    a={'name':'账号 A','account_id':'a219','role_label':'学习者'};b={'name':'账号 B','account_id':'b219','role_label':'学习者'}
    base={'version':1,'theme':'sakura','font_scale':1,'reduce_motion':True,'analogy_domains':[],'teacher_style':'warm','detail':'normal','examples':'normal','refresh_time':'04:00','reminder_enabled':False,'reminder_time':'20:00','show_plan_name_in_notification':True,'chat_persona':'warm'}
    count=0;lock=threading.Lock();detail_count=0;plan_list_count=0;draft_count=0;provider_count=0;rag_source_count=0;rag_get_count=0;bootstrap_count=0;fail_next_settings=False
    def register(n,f):bridge.register(n,f)
    def test_push(p,c):
        bridge.push(p['name'],p.get('data'))
        return {'ok':True}
    register('test.push',test_push)
    def fail_settings(p,c):
        nonlocal fail_next_settings
        fail_next_settings=True
        return {'ok':True}
    register('test.fail_settings',fail_settings)
    def stats(p,c):return {'settings':count,'plans':plan_list_count,'drafts':draft_count,'providers':provider_count,'rag_sources':rag_source_count,'rag_gets':rag_get_count}
    register('test.stats',stats)
    def bootstrap(p,c):
        nonlocal bootstrap_count
        with lock:bootstrap_count+=1;own=bootstrap_count
        if own==1:time.sleep(1.4)
        if own==4:time.sleep(1.0)
        return {'version':'test','account':a,'settings':None if own==4 else base,'options':{},'notice':None,'ready':own not in (1,4)}
    register('app.bootstrap',bootstrap)
    def settings(p,c):
        nonlocal count,fail_next_settings
        with lock: count+=1;own=count
        if fail_next_settings:
            fail_next_settings=False
            raise BridgeError('OFFLINE_TEST','本地模拟设置读取失败')
        time.sleep(.8 if own==1 else .12)
        return {'settings':{**base,'theme':'sakura' if own==1 else 'aurora'},'options':{}}
    register('settings.get',settings);register('sync.status',lambda p,c:None);register('models.status',lambda p,c:{'state':'unknown','short':'待验证'})
    register('update.status',lambda p,c:{'available':False,'busy':False,'state':'UP_TO_DATE','version':'test','current':'test'})
    register('update.automatic',lambda p,c:{'available':False,'busy':False,'state':'UP_TO_DATE','version':'test','current':'test'})
    def plans(p,c):
        nonlocal plan_list_count
        with lock:plan_list_count+=1;own=plan_list_count
        if own==2:
            time.sleep(.8)
            row={'id':'old-plan','title':'旧账号计划','goal':'old','final_goal':'old','state':'active','archived':False,'module_count':1,'window':None,'total_minutes':0,'today':None}
            return {'plans':[row],'study_day':'old-day'}
        if own>=3:
            time.sleep(.12)
            row={'id':'new-plan','title':'新账号计划','goal':'new','final_goal':'new','state':'active','archived':False,'module_count':1,'window':None,'total_minutes':0,'today':None}
            return {'plans':[row],'study_day':'new-day'}
        return {'plans':[],'study_day':'219-test'}
    register('plans.list',plans)
    def current_draft(p,c):
        nonlocal draft_count
        with lock:draft_count+=1;own=draft_count
        if own==1:time.sleep(.8);name='旧账号草稿'
        else:time.sleep(.12);name='新账号草稿'
        return {'draft':{'plan_id':f'draft-{own}','phase':'form','form':None,'name':name,'goal':name,'messages':[],'sources':[],'outline':None,'generation':{},'busy':None}}
    register('plan.current',current_draft)
    def providers(p,c):
        nonlocal provider_count
        with lock:provider_count+=1;own=provider_count
        time.sleep(.8 if own==1 else .12)
        name='旧账号厂商' if own==1 else '新账号厂商'
        return {'providers':[{'id':'test','name':name,'default_base_url':'https://invalid.local','default_model':'offline','search_supported':False,'search_verified':False}]}
    register('models.providers',providers)
    register('models.list',lambda p,c:{'presets':[],'active_id':None})
    docs=[{'id':'doc-a','title':'资料 A','source':'本地','plan_ids':[],'status':'ready','total_chunks':1,'uploaded_chunks':1},{'id':'doc-b','title':'资料 B','source':'本地','plan_ids':[],'status':'ready','total_chunks':1,'uploaded_chunks':1}]
    sessions=[{'id':'a','title':'会话 A','document_ids':['doc-a'],'updated_at':'2026-10-28T01:00:00','count':0},{'id':'b','title':'会话 B','document_ids':['doc-b'],'updated_at':'2026-10-28T02:00:00','count':0}]
    def rag_sources(p,c):
        nonlocal rag_source_count
        with lock:rag_source_count+=1;own=rag_source_count
        rows=[{'id':'a','title':'旧账号资料会话','document_ids':['doc-a'],'updated_at':'2026-10-28T01:00:00','count':0}]
        if own==2:rows=[{'id':'b','title':'新账号资料会话','document_ids':['doc-b'],'updated_at':'2026-10-28T02:00:00','count':0}]
        if own>=3:rows=[{'id':'a','title':'会话 A','document_ids':['doc-a'],'updated_at':'2026-10-28T01:00:00','count':0},{'id':'b','title':'会话 B','document_ids':['doc-b'],'updated_at':'2026-10-28T02:00:00','count':0}]
        return {'plans':[],'documents':docs,'sessions':rows,'server_status':{'state':'ready'}}
    register('rag.sources',rag_sources)
    def rag_get(p,c):
        nonlocal rag_get_count
        with lock:rag_get_count+=1;own=rag_get_count
        if own==1:time.sleep(.8);title='旧账号资料会话';doc='doc-a'
        elif own==2:time.sleep(.12);title='新账号资料会话';doc='doc-b'
        elif p['session_id']=='a':time.sleep(.8);title='会话 A';doc='doc-a'
        else:time.sleep(.12);title='会话 B';doc='doc-b'
        row={'id':p['session_id'],'title':title,'document_ids':[doc],'updated_at':'2026-10-28T01:00:00','count':0}
        return {'session':{**row,'messages':[]}}
    register('rag.get',rag_get)
    def plan_detail(p,c):
        nonlocal detail_count
        with lock:detail_count+=1;own=detail_count
        if own==2:
            time.sleep(.7);raise BridgeError('OFFLINE_TEST','旧刷新失败')
        if own==3:time.sleep(.12)
        title='较新详情' if own>=3 else '初始详情'
        return {'plan':{'id':'plan-219','title':title,'goal':'离线竞态','state':'ready','window':None,'total_minutes':0},'stages':[],'point_links':[],'point_links_status':'ready','daily':{'full_day':1,'cap':30},'sources':[],'point_minutes_status':'none','preparation_status':'none'}
    register('plan.detail',plan_detail);register('classroom.gaps',lambda p,c:[]);register('settings.teacher_presets',lambda p,c:{'presets':[]})
    view.resize(1100,850);view.show();view.setUrl(QUrl.fromLocalFile(str(root/'dist/index.html')))
    timer=QTimer();timer.timeout.connect(lambda:app.quit() if (root/'done').exists() else None);timer.start(100)
    try:app.exec()
    finally:bridge.close()

def test_frontend219_three_real_components_ignore_stale_responses(tmp_path):
    root=tmp_path/'frontend219';build(root)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env=dict(os.environ,TEMP=str(root),TMP=str(root),QT_QPA_PLATFORM='offscreen',QTWEBENGINE_REMOTE_DEBUGGING=f'127.0.0.1:{port}',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu --remote-allow-origins=*',MF_CDP_ENDPOINT=f'http://127.0.0.1:{port}',PYTHONDONTWRITEBYTECODE='1',PATH=str(NODE.parent)+os.pathsep+os.environ.get('PATH',''))
    with (root/'qt.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__)),'--host',str(root)],env=env,stdout=log,stderr=log)
        try:
            result=subprocess.run([str(NODE),str(Path(__file__).with_name('async_frontend219.mjs')),str(root)],env=env,capture_output=True,text=True,encoding='utf-8',timeout=100)
            assert result.returncode==0,result.stdout+result.stderr
        finally:(root/'done').touch();child.wait(timeout=15)
    result=json.loads((root/'async-result219.json').read_text(encoding='utf-8'))
    assert result['ok'],result

if __name__=='__main__' and len(sys.argv)>1 and sys.argv[1]=='--host':host(Path(sys.argv[2]))
