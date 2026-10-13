from tests.refactor.gateway.test_gateway import configured, answer, START, END
from refactor.learning.connections import providers


def test_gateway109_asr_small_non_thinking_foreground_call(tmp_path,vault,monkeypatch):
    gateway,_=configured(tmp_path)
    calls=[]
    def complete(provider,**kwargs):
        calls.append(kwargs)
        return answer(model=kwargs['model'])
    monkeypatch.setattr(providers,'complete',complete)
    try:
        gateway.complete('asr_correction',[dict(role='user',content='项梁那个')],max_tokens=80)
        assert calls[0]['max_tokens']==80 and 0<calls[0]['timeout']<=3
        assert calls[0]['reasoning_effort']=='none'
        summary=gateway.usage_log.summary(START,END,group_by='purpose')
        assert summary['total']['calls']==1
    finally: gateway.close()
