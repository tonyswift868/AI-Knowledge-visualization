"""Explicit-budget real DeepSeek check; local RAG fake, no server/model keys exported."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--preset-root', type=Path, required=True)
    args=parser.parse_args()
    root=args.root.resolve()
    if not root.is_relative_to(Path('D:/MentorFlow/.local/test-tmp/ragui155').resolve()):
        raise ValueError('Use the task scratch directory')
    root.mkdir(parents=True,exist_ok=True)
    import httpx
    from refactor.learning.app.rag import RagService
    from refactor.learning.course.store import CourseStore
    from tests.refactor.simulation.run import live_factory
    from tests.refactor.simulation.engine import Evidence, RecordedGateway
    from tests.refactor.rag155.test_rag155_backend import FakeRagServer, rag155_session
    data=root/'data'
    real=live_factory(args.preset_root,args.account_id)(SimpleNamespace(data_root=data))
    evidence=Evidence(root/'calls')
    gateway=RecordedGateway(real,evidence,budget='1',allowed={'rag_qa'})
    state=rag155_session()
    store=CourseStore(state.identity.account_id,data)
    server=FakeRagServer()
    service=RagService(store=store,account_id=state.identity.account_id,
        session_provider=lambda:state,
        api_base_url='https://rag155.invalid',ca_bundle=REPO/'refactor/desktop/resources/trust/account-api.pem',
        gateway=gateway,http_transport=httpx.MockTransport(server))
    files={
        '数据结构校验资料.md':'# 队列与栈\n\n队列按先进先出（FIFO）顺序移除元素。栈按后进先出（LIFO）顺序移除元素。\n\n本资料没有列出生活类比。\n',
        '压强校验资料.md':'# 压强\n\n压强等于压力除以受力面积，公式为 p=F/S。压力的单位是牛顿，受力面积的单位是平方米，压强的单位是帕斯卡。\n\n在受力面积不变时，压力增大，压强增大。\n',
    }
    paths=[]
    for name,text in files.items():
        p=root/name;p.write_text(text,encoding='utf-8');paths.append(str(p.resolve()))
    service._picked_paths.update(paths)  # Native picker substituted with these test-owned paths.
    progress=[];rows=[]
    try:
        imported=service.import_files(paths,on_progress=progress.append)['documents']
        ids=[d['id'] for d in imported]
        service.ingest(ids,on_progress=progress.append)
        server.search_passages=[dict(document_id=doc_id,**chunk,score=1.0)
            for doc_id,doc in server.documents.items() for chunk in doc['chunks'].values()]
        session=service.create(ids)['session']
        questions=[
            '资料中的压强公式是什么？压力100牛顿、面积2平方米时压强是多少？请给出处。',
            '队列与栈的出入顺序有什么区别？各给一个生活例子；资料未提供的例子请标为资料外补充。',
            '这些资料有没有写CPU的最高允许温度是多少？如果没写请直接说明。',
        ]
        for question in questions:
            start=time.monotonic();deltas=[]
            session=service.send(session['id'],question,cancel=threading.Event(),on_text=deltas.append)['session']
            answer=session['messages'][-1]
            row=dict(question=question,answer=answer,seconds=round(time.monotonic()-start,3),stream_events=len(deltas))
            rows.append(row)
            (root/'live155.json').write_text(json.dumps(dict(session=session,questions=rows,progress=progress,
                sources=service.sources(),spent_peak_cny=str(gateway.spent),calls=evidence.calls),ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(row,ensure_ascii=False),flush=True)
        print(json.dumps(dict(calls=len(evidence.calls),spent_peak_cny=str(gateway.spent)),ensure_ascii=False),flush=True)
    finally:
        service.close();store.close();gateway.close()


if __name__=='__main__':main()
