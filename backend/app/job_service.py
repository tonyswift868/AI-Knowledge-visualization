from __future__ import annotations
import uuid
from datetime import datetime,timezone
from .orm import SessionLocal,JobORM,init_orm
from .observability import observe_job,record_job_queued
def job_id():return 'job_'+uuid.uuid4().hex[:14]
def create_job(workspace_id,kind):
    init_orm(); jid=job_id()
    with SessionLocal() as s:s.add(JobORM(id=jid,workspace_id=workspace_id,job_type=kind,status='queued',stage='validating',progress=0,message='排队中'));s.commit()
    record_job_queued(jid,kind)
    return jid
def set_job(jid,**kw):
    with SessionLocal() as s:
        j=s.get(JobORM,jid)
        if j:
            for k,v in kw.items():setattr(j,k,v)
            s.commit()
def run_job(jid,stages,work):
    with SessionLocal() as s:
        job=s.get(JobORM,jid)
        kind=str(getattr(job,'job_type','default') or 'default')
        workspace_id=str(getattr(job,'workspace_id','') or '') or None
    with observe_job(jid,kind,workspace_id=workspace_id):
        set_job(jid,status='running')
        for i,(stage,message) in enumerate(stages):set_job(jid,stage=stage,message=message,progress=int(i/len(stages)*100))
        try:r=work();set_job(jid,status='succeeded',stage='saving',progress=100,message='已完成');return r
        except Exception as e:set_job(jid,status='failed',error=type(e).__name__,progress=100,message='处理失败');raise
