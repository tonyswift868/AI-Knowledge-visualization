import threading
from types import SimpleNamespace

from tests.refactor.app.test_app_voice_input import Harness, voice_qapp, until


def test_voice109_final_correction_worker_raw_preserved_and_stale_result_ignored(voice_qapp,tmp_path):
    h=Harness(voice_qapp,tmp_path)
    calls=[]
    class Gateway:
        def complete(self,purpose,messages,**kwargs):
            calls.append(threading.get_ident())
            return SimpleNamespace(text='向量那个。')
    h.voice._gateway_provider=lambda:Gateway()
    try:
        h.call('start',session_id='asr109',terms=['向量'],question='哪一边擅长？')
        h.engines[-1].partial='下料来个。'
        request=h.send('stop',session_id='asr109')
        until(voice_qapp,lambda:h.engines[-1].finishing)
        h.engines[-1].complete()
        response=h.response(request)
        assert response['ok'] and response['result']['text']=='向量那个。'
        assert response['result']['raw_text']=='下料来个。'
        assert calls and calls[0]!=threading.get_ident()
        old=len(h.events('final'))
        h.call('start',session_id='next')
        h.voice._corrected('asr109',dict(text='late'))
        assert len(h.events('final'))==old
    finally: h.close()
