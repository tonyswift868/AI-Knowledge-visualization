from __future__ import annotations
import uuid
from datetime import datetime,timezone
from .orm import SessionLocal,JobORM,init_orm
def job_id():return 'job_'+uuid.uuid4().hex[:14]
def create_job(workspace_id,kind):
    init_orm(); jid=job_id()
    with SessionLocal() as s:s.add(JobORM(id=jid,workspace_id=workspace_id,job_type=kind,status='queued',stage='validating',progress=0,message='排队中'));s.commit()
    return jid
def set_job(jid,**kw):
    with SessionLocal() as s:
        j=s.get(JobORM,jid)
        if j:
            for k,v in kw.items():setattr(j,k,v)
            s.commit()
def run_job(jid,stages,work):
    set_job(jid,status='running')
    for i,(stage,message) in enumerate(stages):set_job(jid,stage=stage,message=message,progress=int(i/len(stages)*100))
    try:r=work();set_job(jid,status='succeeded',stage='saving',progress=100,message='已完成');return r
    except Exception as e:set_job(jid,status='failed',error=str(e),progress=100,message='处理失败');raise
