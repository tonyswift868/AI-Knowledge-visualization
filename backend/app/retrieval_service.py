from __future__ import annotations
import re
from sqlalchemy import select
from .orm import SessionLocal,SourceORM,ChunkORM
try: import jieba
except Exception: jieba=None
try: from rank_bm25 import BM25Okapi
except Exception: BM25Okapi=None
def tokenize(text:str):
    pieces=list(jieba.cut(text)) if jieba else re.findall(r'[\w]+|[\u4e00-\u9fff]',text.lower())
    return [x.lower().strip() for x in pieces if x.strip()]
class RetrievalService:
    def search(self,workspace_id:str,query:str,top_k:int=5):
        if not query.strip(): return []
        with SessionLocal() as s:
            rows=s.execute(select(ChunkORM,SourceORM).join(SourceORM,SourceORM.id==ChunkORM.source_id).where(SourceORM.workspace_id==workspace_id)).all()
        if not rows:return []
        q=tokenize(query); corpus=[tokenize(c.text+' '+c.heading) for c,_ in rows]
        scores=BM25Okapi(corpus).get_scores(q) if BM25Okapi else [sum(t in x for t in q) for x in corpus]
        ranked=[]
        for (chunk,source),base in zip(rows,scores):
            title_boost=sum(1 for t in q if t in tokenize(source.title+' '+chunk.heading))*2
            trust={'authoritative':1.2,'reference':1.0,'personal_notes':.9}.get(source.trust_level,1)
            ranked.append((float(base+title_boost)*trust,chunk,source))
        ranked.sort(key=lambda x:x[0],reverse=True); seen=set(); out=[]
        for score,chunk,source in ranked:
            if chunk.content_hash in seen:continue
            seen.add(chunk.content_hash); out.append({'score':round(score,4),'source_id':source.id,'source_title':source.title,'trust_level':source.trust_level,'chunk_id':chunk.id,'page_start':chunk.page_start,'page_end':chunk.page_end,'heading':chunk.heading,'quote':chunk.text[:500],'text':chunk.text})
            if len(out)>=top_k:break
        return out
