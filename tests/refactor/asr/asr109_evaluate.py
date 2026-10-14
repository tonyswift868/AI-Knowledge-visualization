"""Explicit --live SAPI-file evaluation using existing simulation accounting.

Not collected by pytest. Never records/exports a credential or uses a microphone.
"""
import argparse
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import wave


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--live',action='store_true',required=True)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--account-id',required=True)
    parser.add_argument('--preset-root',type=Path,required=True)
    parser.add_argument('--text-only',action='store_true',help='Only the two saved user transcripts; do not repeat the audio run')
    args=parser.parse_args();root=args.root.resolve()
    assert root.is_relative_to(Path('D:/MentorFlow/.local/test-tmp').resolve())
    from PySide6.QtCore import QObject,QTimer,Signal
    from PySide6.QtWidgets import QApplication
    from refactor.learning.app.bridge import AppBridge,CallContext
    from refactor.learning.app.voice_input import VoiceInput
    from refactor.learning.voice.asr.glossary import correct
    from refactor.learning.voice.asr.lesson import protected_vocabulary
    from refactor.learning.voice.asr.correction import finish
    from tests.refactor.simulation.engine import Evidence,RecordedGateway
    from tests.refactor.simulation.run import live_factory
    from refactor.learning.connections.errors import ModelError
    class OptionalRecorder(RecordedGateway):
        def complete(self,purpose,*a,**kw):
            try: return super().complete(purpose,*a,**kw)
            except ModelError as exc:
                # An optional correction falling back is expected, not a dead
                # classroom. Retain every charged bound and the shared ¥1 limit.
                if purpose=='asr_correction' and exc.code in {'MODEL_TIMEOUT','MODEL_CANCELLED',
                    'MODEL_OUTPUT_TRUNCATED','MODEL_RESPONSE_INVALID','MODEL_CONNECTION_FAILED'}:
                    with self.lock: self.halted.clear()
                raise
    services=SimpleNamespace(data_root=root/'live/data')
    real=live_factory(args.preset_root,args.account_id)(services)
    evidence=Evidence(root/'live')
    gateway=OptionalRecorder(real,evidence,budget='1',allowed={'asr_correction'})
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    model=Path(json.loads((root/'environment.json').read_text())['model_path'])
    corpus=json.loads((root/'corpus.json').read_text(encoding='utf-8'))
    protected=protected_vocabulary([c['text'] for c in corpus['chunks']])
    class Manager:
        def status(self): return dict(installed=True,path=str(model),model_name='existing-read-only',size_bytes=0,download_url='')
    state={}
    class FileRecorder(QObject):
        audio_ready=Signal(bytes);error=Signal(str,str)
        def __init__(self,parent=None):
            super().__init__(parent);self.offset=0;self.timer=QTimer(self);self.timer.setInterval(20);self.timer.timeout.connect(self.tick)
        def start(self): self.timer.start()
        def tick(self):
            chunk=state['audio'][self.offset:self.offset+640];self.offset+=len(chunk)
            if chunk:self.audio_ready.emit(chunk)
            else:
                self.timer.stop();state['stop']()
        def stop(self): self.timer.stop()
        cancel=stop
    bridge=AppBridge()
    voice=VoiceInput(bridge,root/'live/data',model_manager=Manager(),recorder_factory=FileRecorder,
                     gateway_provider=lambda:gateway)
    rows=[] if args.text_only else json.loads((root/'local.json').read_text(encoding='utf-8'));results=[]
    try:
        for row in rows:
            with wave.open(str(root/'wav'/(row['id']+'.wav'))) as w:state['audio']=w.readframes(w.getnframes())
            done=threading.Event();box={};cancel=threading.Event()
            def stop():
                box['started']=time.monotonic()
                def worker():
                    try: box['value']=voice.stop({'session_id':row['id']},CallContext(row['id'],cancel,lambda _:None))
                    except Exception as exc:box['error']=getattr(exc,'code',type(exc).__name__)
                    finally:box['seconds']=time.monotonic()-box['started'];done.set()
                threading.Thread(target=worker,daemon=True).start()
            state['stop']=stop
            with (root/'report.md').open('a',encoding='utf-8') as f:
                f.write(f"\n付费前 {row['id']}：仅在可疑读音/拼写触发时改术语，预期改善拼写且不补答案；原识别 {row['raw']}。\n")
            voice.start(dict(session_id=row['id'],terms=row['terms'],protected=list(protected),question=row['question']),None)
            until=time.monotonic()+45
            while not done.is_set() and time.monotonic()<until:app.processEvents();time.sleep(.005)
            if not done.is_set():raise RuntimeError('voice evaluation stalled')
            if 'error' in box:raise RuntimeError(box['error'])
            result=box['value'];raw=result['raw_text']
            local=correct(raw,row['terms'],protect_existing=False,protected_terms=protected,lesson_match=True)[0]
            results.append(dict(id=row['id'],raw=raw,local=local,combined=result['text'],stop_seconds=box['seconds'],
                                matches_saved_raw=raw==row['raw']))
            (root/'evaluated.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(row['id'],raw,'=>',result['text'],round(box['seconds'],3),flush=True)
            if gateway.halted.is_set():break
        supplemental=[]
        for row in json.loads((root/'text-cases.json').read_text(encoding='utf-8')):
            local=correct(row['raw'],row['terms'],protect_existing=False,protected_terms=protected,lesson_match=True)[0]
            with (root/'report.md').open('a',encoding='utf-8') as f:f.write(f"\n付费前 {row['id']}：仅修术语和同音字；推测原意不作为补写依据，不改‘长得比较矮/用制力’。\n")
            result=finish(row['raw'],local,row['terms'],row['question'],gateway,protected_terms=protected)
            supplemental.append(dict(row,local=local,combined=result.text,called=result.called,outcome=result.outcome,seconds=result.seconds))
        (root/'supplement-results.json').write_text(json.dumps(supplemental,ensure_ascii=False,indent=2),encoding='utf-8')
        deadline=time.monotonic()+5
        while gateway.reserved and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        (root/'live-summary.json').write_text(json.dumps(dict(calls=gateway.attempted,charged_bound=str(gateway.spent),reserved=str(gateway.reserved)),indent=2),encoding='utf-8')
        print('SUMMARY',gateway.attempted,str(gateway.spent),flush=True)
    finally:voice.dispose();bridge.close();real.close();app.processEvents()


if __name__=='__main__':main()
