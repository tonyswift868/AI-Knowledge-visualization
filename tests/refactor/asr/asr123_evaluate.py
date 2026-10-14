"""Explicit live opt-in: two frozen 122 file recordings through real VoiceInput.

Prepare cases.json/audio-index.json with the 122 S02 clean recordings at rates
0/2 under the task root. No microphone, reference answer or credential access.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import wave

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
sys.dont_write_bytecode=True


def run(root,account,preset_root):
    from PySide6.QtCore import QCoreApplication,QObject,Signal
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.app.voice_input import VoiceInput
    from refactor.learning.voice.asr.lesson import protected_vocabulary
    from tests.refactor.simulation.run import live_factory
    from tests.refactor.simulation.engine import Evidence,RecordedGateway
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    cases=read(root/'cases.json');index={r['id']:r for r in read(root/'audio-index.json')}
    model=Path(read(root.parent/'asr109/environment.json')['model_path'])
    protected=protected_vocabulary([r['text'] for r in read(root.parent/'asr109/corpus.json')['chunks']])
    evidence=Evidence(root/'live');gateway=None;cache={};logical=[]
    class CachedLive:
        def complete(self,purpose,messages,**kw):
            nonlocal gateway
            key=json.dumps([purpose,messages,kw.get('system')],ensure_ascii=False,sort_keys=True)
            if key in cache:
                text,seconds=cache[key]
                kw['cancel'].wait(seconds)
                logical.append(dict(source='same-run-cache',seconds=seconds,text=text))
                return SimpleNamespace(text=text)
            if gateway is None:
                real=live_factory(preset_root,account)(SimpleNamespace(data_root=root/'live/data'))
                gateway=RecordedGateway(real,evidence,budget='.05',allowed={'asr_correction'})
            started=time.perf_counter()
            result=gateway.complete(purpose,messages,**kw)
            seconds=time.perf_counter()-started
            cache[key]=(result.text,seconds)
            logical.append(dict(source='live',seconds=seconds,text=result.text))
            return result
    class Manager:
        def status(self):return dict(installed=True,path=str(model))
    class Recorder(QObject):
        audio_ready=Signal(bytes);error=Signal(str,str)
        def start(self):pass
        def stop(self):pass
        def cancel(self):pass
    app=QCoreApplication.instance() or QCoreApplication([])
    bridge=AppBridge();events=[];arrived={}
    def receive(text):
        e=json.loads(text);events.append(e)
        if e.get('name')=='voice.final':arrived[e['data']['session_id']]=time.perf_counter()
    bridge.event.connect(receive)
    voice=VoiceInput(bridge,root/'ui-data',model_manager=Manager(),recorder_factory=Recorder,
        gateway_provider=CachedLive)
    output=[]
    try:
        for case in cases:
            id=case['id']
            voice.start(dict(session_id=id,terms=case['vocabulary'],question=case['question'],protected=list(protected)),None)
            with wave.open(index[id]['path']) as wav:pcm=wav.readframes(wav.getnframes())
            started=time.perf_counter()
            for offset in range(0,len(pcm),640):
                due=started+offset/32000
                while time.perf_counter()<due:app.processEvents();time.sleep(.001)
                voice.service._recorder.audio_ready.emit(pcm[offset:offset+640]);app.processEvents()
            due=started+len(pcm)/32000
            while time.perf_counter()<due:app.processEvents();time.sleep(.001)
            stopped=time.perf_counter();done=threading.Event();box={}
            def stop():
                try:box['result']=voice.stop(dict(session_id=id),SimpleNamespace(cancel=threading.Event()))
                except Exception as exc:box['error']=getattr(exc,'code',type(exc).__name__)
                finally:done.set()
            threading.Thread(target=stop,daemon=True).start()
            # Evaluation guard only, longer than the product's bridge task guard.
            deadline=time.monotonic()+100
            while (not done.is_set() or id not in arrived) and time.monotonic()<deadline:
                app.processEvents();time.sleep(.002)
            assert done.is_set() and id in arrived and 'result' in box,box
            output.append(dict(id=id,before=case['combined'],before_seconds=case['stop_seconds'],
                after=box['result'],seconds=arrived[id]-stopped))
            print(id,box['result']['text'],round(output[-1]['seconds'],3),flush=True)
            if gateway is not None and gateway.halted.is_set():break
    finally:
        voice.dispose();bridge.close()
        result=dict(rows=output,logical_calls=logical,calls=evidence.calls,
            peak_cost=str(gateway.spent) if gateway else '0',records=[read(p) for p in sorted((root/'live/model').glob('call-*.json'))])
        (root/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if gateway:gateway.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true',required=True)
    p.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/asr123'))
    p.add_argument('--account-id',required=True);p.add_argument('--preset-root',type=Path,required=True)
    a=p.parse_args();assert a.root.resolve()==Path('D:/MentorFlow/.local/test-tmp/asr123').resolve()
    run(a.root,a.account_id,a.preset_root)
