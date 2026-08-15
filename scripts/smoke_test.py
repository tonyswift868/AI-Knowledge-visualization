from __future__ import annotations
import sys
import tempfile
from datetime import date,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from fastapi.testclient import TestClient
from app import active_teaching, db, orm
from app.main import app

def main():
    temp_parent=Path(r'E:\Codex\Temp'); temp_parent.mkdir(parents=True,exist_ok=True)
    old_db,old_data,old_orm,old_upload=db.DB_PATH,db.DATA_DIR,orm.ORM_DB,active_teaching.UPLOAD_ROOT
    with tempfile.TemporaryDirectory(prefix='mentorflow-smoke-',dir=temp_parent) as temporary:
        isolated=Path(temporary); db.DB_PATH=isolated/'app.db'; db.DATA_DIR=isolated; active_teaching.UPLOAD_ROOT=isolated/'active-teaching-uploads'; orm.configure_orm(isolated/'mentorflow_v2.db')
        try:
            with TestClient(app) as c:
                ws=c.post('/api/workspaces',json={'title':'Smoke Workspace','description':'automated'}); assert ws.status_code==200,ws.text; wid=ws.json()['id']
                src=c.post(f'/api/workspaces/{wid}/sources/text',json={'title':'Smoke Source','text':'# Retrieval\n\nBM25 检索需要把资料切成可引用证据，用户可以通过真实任务验证理解。','trust_level':'reference'}); assert src.status_code==200,src.text
                goal={'goal_statement':'在截止日期前掌握可检索证据并完成验证成果','motivation':'smoke','deadline':(date.today()+timedelta(days=14)).isoformat(),'minutes_per_day':30,'days_per_week':5,'preferred_weekdays':[0,1,2,3,4],'current_level':'beginner','expected_output':'验证成果','success_criteria':['完成最终任务']}
                assert c.post(f'/api/workspaces/{wid}/goal',json=goal).status_code==200
                a=c.post(f'/api/workspaces/{wid}/feasibility/analyze'); assert a.status_code==200,a.text
                assert c.post(f'/api/workspaces/{wid}/feasibility/confirm',json={'confirmed':True}).status_code==200
                plan=c.post(f'/api/workspaces/{wid}/plans/generate',json={'provider_config':{'provider':'fake'}}); assert plan.status_code==200,plan.text; p=plan.json(); assert p['milestones'] and p['study_days']
                assert c.post(f'/api/plans/{p["id"]}/activate').status_code==200
                session=c.post(f'/api/workspaces/{wid}/sessions',json={'lesson_id':p['milestones'][0]['modules'][0]['lessons'][0]['id']}); assert session.status_code==200
                msg=c.post(f'/api/sessions/{session.json()["id"]}/messages',json={'message':'资料中的核心是什么？'}); assert msg.status_code==200 and msg.json()['status'] in {'grounded','insufficient'}
                ev=c.post(f'/api/lessons/{p["milestones"][0]["modules"][0]["lessons"][0]["id"]}/evaluate',json={'response':'我依据资料解释了证据和验证步骤。','evidence':'资料证据'}); assert ev.status_code==200
                task=p['study_days'][0]['tasks'][0]; assert c.post(f'/api/tasks/{task["id"]}/start').status_code==200; assert c.post(f'/api/tasks/{task["id"]}/complete').status_code==200
                progress=c.get(f'/api/workspaces/{wid}/progress'); assert progress.status_code==200 and 'completion_rate' in progress.json()
                repl=c.post(f'/api/plans/{p["id"]}/replan',json={'reason':'missed_tasks','new_minutes_per_day':25}); assert repl.status_code==200 and repl.json()['changes']
        finally:
            orm.engine.dispose(); orm.configure_orm(old_orm)
            db.DB_PATH,db.DATA_DIR=old_db,old_data; active_teaching.UPLOAD_ROOT=old_upload
    print('SMOKE PASSED (isolated): workspace -> source -> goal -> feasibility -> fake plan -> activate -> tutor -> evaluate -> complete -> progress -> replan')
if __name__=='__main__': main()
