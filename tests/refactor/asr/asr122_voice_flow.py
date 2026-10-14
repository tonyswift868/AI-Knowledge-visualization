"""Three real VoiceInput/AsrService/AsrEngine file recordings; cached model only."""
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


def run(root):
    from PySide6.QtCore import QCoreApplication,QObject,Signal
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.app.voice_input import VoiceInput
    from refactor.learning.voice.asr.lesson import protected_vocabulary
    read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    contexts={r['id']:r for r in read(root/'sentences-input.json')}
    cache={}
    for k,v in read(root/'correction-cache-results.json').items():
        local,terms,q=json.loads(k);cache[json.dumps([local,terms[:40],q[-600:]],ensure_ascii=False)]=v
    # Include historical entries that needed no new paid evaluation.
    for row in read(root/'after-combined-results.json'):
        if row['model_called']:
            original=next(r for r in read(root/'after-results.json') if r['id']==row['id'])
            q=original['question'];terms=original['vocabulary']
            cache.setdefault(json.dumps([original['local'],terms[:40],q[-600:]],ensure_ascii=False),
                             dict(text=row['combined'],seconds=row['model_seconds']))
    missed=[]
    class CachedGateway:
        def complete(self,purpose,messages,**kw):
            assert purpose=='asr_correction'
            data=json.loads(messages[0]['content'])
            key=json.dumps([data['recognized'],data['terms'],data['teacher_question']],ensure_ascii=False)
            item=cache.get(key)
            if item is None:
                missed.append(data['recognized']);raise RuntimeError('No exact cached input; no network')
            kw['cancel'].wait(item['seconds'])
            return SimpleNamespace(text=item['text'])
    model=Path(read(root.parent/'asr109/environment.json')['model_path'])
    protected=protected_vocabulary([r['text'] for r in read(root.parent/'asr109/corpus.json')['chunks']])
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
        event=json.loads(text);events.append(event)
        if event.get('name')=='voice.final':arrived[event['data']['session_id']]=time.perf_counter()
    bridge.event.connect(receive)
    voice=VoiceInput(bridge,root/'ui-data',model_manager=Manager(),recorder_factory=Recorder,
                     gateway_provider=CachedGateway)
    audio_index={r['id']:r for r in read(root/'audio-index.json')};out=[]
    try:
        for id in ['S01-v0-r0','S02-v0-r0','U02-v0-r0']:
            meta=audio_index[id];context=contexts[meta['sentence_id']]
            voice.start(dict(session_id=id,terms=context['vocabulary'],question=context['question'],protected=list(protected)),None)
            with wave.open(meta['path']) as w:pcm=w.readframes(w.getnframes())
            started=time.perf_counter()
            for offset in range(0,len(pcm),640):
                due=started+offset/32000
                while time.perf_counter()<due:
                    app.processEvents();time.sleep(max(0.,min(.002,due-time.perf_counter())))
                voice.service._recorder.audio_ready.emit(pcm[offset:offset+640]);app.processEvents()
            due=started+len(pcm)/32000
            while time.perf_counter()<due:app.processEvents();time.sleep(.001)
            box={};done=threading.Event();stopped=time.perf_counter()
            def finish():
                try:box['result']=voice.stop(dict(session_id=id),SimpleNamespace(cancel=threading.Event()))
                except Exception as exc:box['error']=getattr(exc,'code',type(exc).__name__)
                finally:box['seconds']=time.perf_counter()-stopped;done.set()
            threading.Thread(target=finish,daemon=True).start()
            deadline=time.monotonic()+15
            while (not done.is_set() or id not in arrived) and time.monotonic()<deadline:
                app.processEvents();time.sleep(.002)
            assert done.is_set() and 'result' in box and id in arrived,box
            final=next(e['data'] for e in reversed(events) if e.get('name')=='voice.final' and e['data']['session_id']==id)
            # QWebChannel JSON turns Python replacement tuples into arrays.
            assert final==json.loads(json.dumps(box['result'])),(final,box['result'])
            out.append(dict(id=id,stop_seconds=max(box['seconds'],arrived[id]-stopped),result=final))
        (root/'ui-flow-results.json').write_text(json.dumps(dict(rows=out,cache_misses=missed),ensure_ascii=False,indent=2),encoding='utf-8')
    finally:voice.dispose();bridge.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/asr122'))
    args=p.parse_args();assert args.root.resolve()==Path('D:/MentorFlow/.local/test-tmp/asr122').resolve();run(args.root)
