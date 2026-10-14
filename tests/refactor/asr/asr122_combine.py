"""Existing 109 correction with exact-input cache and a shared CNY .10 budget.

Historic results are replayed with their measured wait; the current product
waits for the gateway result (123 removed the stop deadline). Reference sentences never enter model requests.
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


def key(local,context):
    return json.dumps([local,context['vocabulary'],context.get('question','')],ensure_ascii=False)


def combine(root,version,account,preset_root):
    from refactor.learning.voice.asr.correction import finish,needs_model
    from refactor.learning.voice.asr.lesson import protected_vocabulary
    from tests.refactor.simulation.engine import Evidence,RecordedGateway
    from tests.refactor.simulation.run import live_factory
    from refactor.learning.connections.errors import ModelError
    contexts={r['id']:r for r in read(root/'sentences-input.json')+read(root/'pause-input.json')}
    protected=protected_vocabulary([c['text'] for c in read(root.parent/'asr109/corpus.json')['chunks']])
    prior=root.parent/'asr110';prior_rows={};cache={}
    for old in read(prior/'combined-results.json'):
        name=old['backend']
        if name not in prior_rows:prior_rows[name]={r['id']:r for r in read(prior/(name+'-results.json'))}
        original=prior_rows[name][old['id']]
        context=contexts.get(original['sentence_id'])
        if context:
            cache[key(original['local'],context)]=dict(text=old['combined'],seconds=old['model_seconds'],
                called=old['model_called'],source='110/'+name,outcome=old['model_outcome'])
    if (root/'correction-cache-results.json').exists():cache.update(read(root/'correction-cache-results.json'))
    path=root/(version+'-combined-results.json')
    output=read(path) if path.exists() else []
    completed={r['id'] for r in output}
    live=None;evidence=None
    class Recorder(RecordedGateway):
        def complete(self,purpose,*args,**kwargs):
            # Unknown-usage failures also fit the .10 ceiling. The normal
            # recorder still reserves observed prices for successful calls.
            config=self.real.get_configuration() or {}
            upper=len((kwargs.get('system','')+json.dumps(args[0],ensure_ascii=False)).encode('utf-8'))+4096
            estimate=self.real.price_catalog.estimate_cost(config['provider_id'],config['model'],
                input_tokens=(upper,upper),output_tokens=(80,80),cache_hit_rate=(0,0),
                context=dict(self.real.estimate_context(),price_period='peak',cache_mode='implicit'))
            if estimate is None or estimate.currency!='CNY':
                self.reject('SIMULATION_UNPRICED','Cannot bound this ASR correction charge',purpose)
            with self.lock:
                if self.spent+self.reserved+estimate.high>self.limit:
                    self.reject('SIMULATION_BUDGET','Unknown-usage bound would exceed CNY .10',purpose)
            try:return super().complete(purpose,*args,**kwargs)
            except ModelError as exc:
                if exc.code in {'MODEL_TIMEOUT','MODEL_CANCELLED','MODEL_OUTPUT_TRUNCATED',
                                 'MODEL_RESPONSE_INVALID','MODEL_CONNECTION_FAILED'}:
                    with self.lock:self.halted.clear()
                raise
    def acquire():
        nonlocal live,evidence
        if live is None:
            evidence=Evidence(root/'live')
            real=live_factory(preset_root,account)(SimpleNamespace(data_root=root/'live/data'))
            live=Recorder(real,evidence,budget='.10',allowed={'asr_correction'})
        return live
    try:
        for row in read(root/(version+'-results.json')):
            if row['id'] in completed:continue
            context=contexts[row['sentence_id']];terms=context['vocabulary'];q=context.get('question','')
            token=key(row['local'],context);old=cache.get(token)
            trigger=needs_model(row['local'],terms,q)
            if not trigger:
                result=SimpleNamespace(text=row['local'],called=False,outcome='local',seconds=0.)
            elif old:
                class Cached:
                    def complete(self,*args,**kwargs):
                        kwargs['cancel'].wait(old['seconds'])
                        return SimpleNamespace(text=old['text'])
                result=finish(row['raw'],row['local'],terms,q,Cached(),protected_terms=protected)
            else:
                with (root/'report.md').open('a',encoding='utf-8') as f:
                    f.write(f'\n付费前 {version}/{row["id"]}：预期仅修复已有读音的术语，保留口语与否定，不补内容；'
                            '80 token，等模型连接完成，整批上限¥0.10。\n')
                client=acquire()
                result=finish(row['raw'],row['local'],terms,q,client,protected_terms=protected)
                cache[token]=dict(text=result.text,seconds=result.seconds,called=result.called,
                                  source='122/'+version,outcome=result.outcome)
                # Do not start another paid call until any cancelled call's
                # accounting completes; an unknown charge is not treated free.
                deadline=time.monotonic()+5
                while live is not None and live.reserved and time.monotonic()<deadline:time.sleep(.02)
            output.append(dict(id=row['id'],group=row['group'],combined=result.text,
                combined_scores=scores(row['reference'],result.text,row['gold_terms']),model_called=result.called,
                model_outcome=result.outcome,model_seconds=result.seconds,cache_source=old['source'] if old else None,
                stop_seconds=row['seconds']+row['local_seconds']+result.seconds))
            write(path,output);write(root/'correction-cache-results.json',cache)
            print(version,row['id'],result.outcome,old['source'] if old else 'new',result.text,flush=True)
            if live is not None and live.spent>=live.limit:raise RuntimeError('CNY .10 budget reached')
            if live is not None and live.halted.is_set():raise RuntimeError('Model evaluation halted; see billing evidence')
    finally:
        if live is not None:
            deadline=time.monotonic()+5
            while live.reserved and time.monotonic()<deadline:time.sleep(.02)
            write(root/'billing-results.json',dict(charged_bound=str(live.spent),reserved=str(live.reserved),calls=live.evidence.calls))
            live.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true',required=True)
    p.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/asr122'))
    p.add_argument('--version',choices=['before','after'],required=True)
    p.add_argument('--account-id',required=True);p.add_argument('--preset-root',type=Path,required=True)
    a=p.parse_args();assert a.root.resolve()==Path('D:/MentorFlow/.local/test-tmp/asr122').resolve()
    combine(a.root,a.version,a.account_id,a.preset_root)


if __name__=='__main__':main()
