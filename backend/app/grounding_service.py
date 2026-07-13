from __future__ import annotations
import re
from .orm import SessionLocal,SourceORM,ChunkORM
from sqlalchemy import select
def norm(s:str):return re.sub(r'\s+',' ',s).strip().lower()
def validate_citation(workspace_id:str,c:dict,allowed_ids:set[str]|None=None):
    with SessionLocal() as s:
        row=s.execute(select(ChunkORM,SourceORM).join(SourceORM,SourceORM.id==ChunkORM.source_id).where(ChunkORM.id==c.get('chunk_id'),SourceORM.workspace_id==workspace_id)).first()
    if not row:return False,'chunk 不存在或不属于当前 workspace'
    chunk,source=row
    if allowed_ids is not None and chunk.id not in allowed_ids:return False,'引用不属于本次允许证据集合'
    if c.get('source_id')!=source.id:return False,'source_id 不匹配'
    if c.get('page_start')!=chunk.page_start or c.get('page_end')!=chunk.page_end:return False,'页码不匹配'
    if norm(c.get('quote','')) not in norm(chunk.text):return False,'quote 不在原文中'
    return True,''
def critic(workspace_id:str,claims:list[dict],allowed_ids:set[str]|None=None):
    errors=[]
    for c in claims:
        ok,reason=validate_citation(workspace_id,c,allowed_ids)
        if not ok:errors.append(reason)
    return {'passed':not errors,'errors':errors,'unsupported_claims':[]}
