"""Replay ASR rows through existing correction, optional paid --live pass <= CNY .5.

Identical (local transcript, lesson terms, teacher question) calls are reused
within this evaluation, explicitly tagged. No reference answer is sent.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
sys.dont_write_bytecode=True
from tests.refactor.asr.asr110_evaluate import read,write,scores


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--live',action='store_true',required=True)
    p.add_argument('--account-id',required=True);p.add_argument('--preset-root',type=Path,required=True)
    p.add_argument('--backends',nargs='+',default=['sensevoice','zipformer','whisper'])
    a=p.parse_args();root=a.root.resolve()
    assert root.is_relative_to(Path('D:/MentorFlow/.local/test-tmp/asr110').resolve())
    from refactor.learning.voice.asr.correction import finish,needs_model
    from refactor.learning.voice.asr.lesson import protected_vocabulary
    from refactor.learning.connections.errors import ModelError
    from tests.refactor.simulation.engine import Evidence,RecordedGateway
    from tests.refactor.simulation.run import live_factory
    class OptionalRecorder(RecordedGateway):
        def complete(self,purpose,*args,**kw):
            try:return super().complete(purpose,*args,**kw)
            except ModelError as exc:
                if exc.code in {'MODEL_TIMEOUT','MODEL_CANCELLED','MODEL_OUTPUT_TRUNCATED',
                                 'MODEL_RESPONSE_INVALID','MODEL_CONNECTION_FAILED'}:
                    with self.lock:self.halted.clear()
                raise
    real=live_factory(a.preset_root,a.account_id)(SimpleNamespace(data_root=root/'live/data'))
    evidence=Evidence(root/'live')
    gateway=OptionalRecorder(real,evidence,budget='0.5',allowed={'asr_correction'})
    sentences={r['id']:r for r in read(root/'sentences-input.json')}
    protected=protected_vocabulary([c['text'] for c in read(root.parent/'asr109/corpus.json')['chunks']])
    cache={}; output=read(root/'combined-results.json') if (root/'combined-results.json').exists() else []
    completed={(r['backend'],r['id']) for r in output}
    all_rows={}
    for old in output:
        name=old['backend']
        if name not in all_rows:all_rows[name]={r['id']:r for r in read(root/(name+'-results.json'))}
        original=all_rows[name][old['id']];context=sentences.get(original['sentence_id'],sentences['S01'])
        key=json.dumps([original['local'],context['vocabulary'],context.get('question','')],ensure_ascii=False)
        cache[key]=dict(text=old['combined'],called=old['model_called'],outcome=old['model_outcome'],seconds=old['model_seconds'])
    try:
        for backend in a.backends:
            for row in read(root/(backend+'-results.json')):
                if (backend,row['id']) in completed:continue
                context=sentences.get(row['sentence_id'],sentences['S01'])
                terms=context['vocabulary'];q=context.get('question','')
                key=json.dumps([row['local'],terms,q],ensure_ascii=False)
                reused=key in cache; start=time.perf_counter()
                if reused: result=cache[key]
                else:
                    if needs_model(row['local'],terms,q):
                        with (root/'report.md').open('a',encoding='utf-8') as f:
                            f.write(f"\n付费前 {backend}/{row['id']}：仅纠正本课术语读音/英文拼写，预期减少残留错词，不恢复已丢失的词；3 秒回退，最多 80 token。\n")
                    value=finish(row['raw'],row['local'],terms,q,gateway,timeout=3,protected_terms=protected)
                    result=dict(text=value.text,called=value.called,outcome=value.outcome,seconds=value.seconds)
                    cache[key]=result
                output.append(dict(backend=backend,id=row['id'],group=row['group'],
                    combined=result['text'],combined_scores=scores(row['reference'],result['text'],row['gold_terms']),
                    reused=reused,model_called=result['called'],model_outcome=result['outcome'],
                    model_seconds=result['seconds'],evaluation_wait=time.perf_counter()-start))
                write(root/'combined-results.json',output)
                print(backend,row['id'],result['outcome'],'cached' if reused else '',result['text'],flush=True)
                # Optional timeout accounting may set/clear halted between the
                # deadline and the worker's finally. Do not mistake that race
                # for exceeding the shared budget. Next paid call still uses
                # RecordedGateway's reservation check.
                if gateway.spent>=gateway.limit:raise RuntimeError('Evaluation budget stopped')
    finally:
        # Let a cancelled optional call finish its accounting before totals.
        end=time.monotonic()+5
        while gateway.reserved and time.monotonic()<end:time.sleep(.05)
        write(root/'live-summary.json',dict(charged_bound=str(gateway.spent),reserved=str(gateway.reserved),
              calls=len(evidence.calls),unique_inputs=len(cache)))
        gateway.close()


if __name__=='__main__':main()
