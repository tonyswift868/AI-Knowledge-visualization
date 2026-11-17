"""Recompute benchmark metrics from saved runs and independent labels; offline."""
import argparse
import json
from pathlib import Path

from metrics420 import cohen_kappa, precision_at_5
from run420 import TOPICS


def summarize(work, labels_path):
    with (work/'runs.json').open(encoding='utf-8') as stream:
        runs=json.load(stream)
    expected={(topic,arm) for topic in TOPICS for arm in ('A','B','C')}
    if len(runs)!=len(expected) or {(r['topic'],r['arm']) for r in runs}!=expected:
        raise ValueError('incomplete topic/arm evaluation; gate cannot be computed')
    labels={}
    with labels_path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                row=json.loads(line)
                if row['id'] in labels:raise ValueError('duplicate label ID')
                labels[row['id']]=row['label']
    result=[]
    for run in runs:
        selected=[labels[cid] for cid in run['selected']]
        top_five=[labels[cid] for cid in run['order'][:5]]
        judgments=run['judgments']
        model=['能用' if j['score']==3 else '勉强' if j['score']==2 else '不能用' for j in judgments]
        human=[labels[j['id']] for j in judgments]
        result.append(dict(topic=run['topic'],arm=run['arm'],usable=selected.count('能用'),
            enough=selected.count('能用')>=3,precision_at_5=precision_at_5(top_five),
            selected_count=len(selected),selected_precision=selected.count('能用')/len(selected) if selected else 0,
            search_calls=run['search_calls'],read_calls=run['read_calls'],model_calls=run['model_calls'],
            cny_upper=run['cny_upper'],seconds=run['elapsed_seconds'],
            rerank_seconds=run['rerank_seconds'],kappa=cohen_kappa(model,human) if model else None,
            judgments=len(judgments),selected_ids=run['selected']))
    # Every searched candidate, not merely selected candidates, must have a label.
    with (work/'blind-pool.jsonl').open(encoding='utf-8') as stream:
        pool_ids={json.loads(line)['id'] for line in stream if line.strip()}
    if pool_ids!=set(labels):raise ValueError('label pool coverage mismatch')
    arms={arm:all(r['enough'] for r in result if r['arm']==arm) for arm in sorted({r['arm'] for r in result})}
    aggregate_kappa={}
    for arm in ('B','C'):
        pairs=[(j,labels[j['id']]) for run in runs if run['arm']==arm for j in run['judgments']]
        model=['能用' if j['score']==3 else '勉强' if j['score']==2 else '不能用' for j,_ in pairs]
        aggregate_kappa[arm]=dict(n=len(pairs),value=cohen_kappa(model,[h for _,h in pairs]) if pairs else None)
    with (work/'costs.json').open(encoding='utf-8') as stream:
        spend=sum(r['cny_upper'] for r in json.load(stream))
    return dict(rows=result,gate=arms,candidates=len(pool_ids),actual_cny_upper=spend,aggregate_kappa=aggregate_kappa,
        notes='B/C share query planning and search; costs/seconds are standalone arm estimates. Kappa covers judged pages only; A has no judge.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--labels',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(summarize(args.work,args.labels),ensure_ascii=False,indent=2))
