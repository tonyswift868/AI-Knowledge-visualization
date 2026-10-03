"""One authorized source-grounded pressure point; MF_PRESSURE37_REAL=1 opts in."""
from contextlib import ExitStack
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from tests.refactor.conversation37.test_grounded_conversation37 import make_environment, act, PLAN

pytestmark=pytest.mark.skipif(os.environ.get('MF_PRESSURE37_REAL')!='1',reason='单点真实调用需显式选择，上限¥1')
OUT=Path(r'D:\MentorFlow\.local\test-tmp\fix37-classroom')

def test_pressure37_one_point_real_grounded(tmp_path):
    from tests.refactor.class_eval import class_eval as ce
    from refactor.learning.app.classroom import Classroom
    from refactor.learning.connections import gateway as gm
    from refactor.learning.connections.errors import ModelError
    OUT.mkdir(parents=True,exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(patch.object(ce,'ROOT',OUT))
        recorded=ce.live_gateway(stack,OUT/'real')
        class Meter:
            operation=''
            def complete(self,purpose,messages,**kw):
                kw['max_tokens']=max(kw.get('max_tokens',4096),gm.PURPOSE_MIN_OUTPUT.get(purpose,0))
                packet=json.dumps(dict(system=kw.get('system'),messages=messages),ensure_ascii=False)
                bound=len(packet.encode())+2048
                ctx=dict(recorded.inner.estimate_context(),cache_mode='implicit',price_period='peak')
                estimate=recorded.inner.price_catalog.estimate_cost(recorded.config['provider_id'],recorded.config['model'],
                    input_tokens=(bound,bound),output_tokens=(kw['max_tokens'],kw['max_tokens']),cache_hit_rate=(0,0),context=ctx)
                spent=sum(Decimal(row.get('peak_cost') or row['reserved']) for row in recorded.rows)
                if estimate is None or spent+estimate.high>Decimal('1'):
                    raise ModelError('EVAL_BUDGET_STOP','最高预留后会超过¥1，未发起调用。')
                with (OUT/'expectations.md').open('a',encoding='utf-8') as f:
                    f.write(f'\n- {self.operation}：预计只口语改述压强原文，不引入硬度条件，每段本点引用；调用前已花¥{spent}，最高预留¥{estimate.high}。\n')
                recorded.case='pressure37';recorded.operation=self.operation
                return recorded.complete(purpose,messages,**kw)
        e=make_environment(tmp_path/'data');stack.callback(e.store.close)
        e.services.gateway=Meter()
        stack.enter_context(patch.object(Classroom,'_materials',staticmethod(lambda:e.materials)))
        snapshots=[]
        for name,args,label in [
            ('next',(),'生活化开场'),('choose',(0,),'顺着正确选项解释'),
            ('confused',(),'没太懂换角度'),('ask',('材料硬度也相同吗？',),'原文没有的条件追问'),
            ('back',(),'回到刚才'),('next',(),'取题库题'),('answer',([0],),'程序判正确'),
            ('why',(),'原文答案解释'),('ask',('面积与压强的关系还能怎么说？',),'收尾追问'),
        ]:
            e.services.gateway.operation=label
            t=time.monotonic();v=act(e,name,*args)
            snapshots.append(dict(action=name,label=label,seconds=time.monotonic()-t,room=v))
            ce.dump(OUT/'real'/'snapshots.json',snapshots)
            assert not v['failure'],v['failure']
        # End this point without starting another. No long student simulation.
        final=act(e,'next')
        assert final['step']=='test_offer'
        ce.dump(OUT/'real'/'messages.json',e.store.messages(PLAN,'m0'))
        lines=['# 压强单点真实对话','', '模型为管理员现有DeepSeek Flash；题库/关联资料是明确替身，非MF04实际集成。','']
        for m in e.store.messages(PLAN,'m0'):
            lines += [f"## {m['role']} / {m['kind']}",'',m['content'],'']
        (OUT/'dialogue.md').write_text('\n'.join(lines),encoding='utf-8')
        ce.dump(OUT/'real'/'summary.json',dict(calls=len(recorded.rows),peak_cost=str(sum(Decimal(r.get('peak_cost') or r['reserved']) for r in recorded.rows))))
