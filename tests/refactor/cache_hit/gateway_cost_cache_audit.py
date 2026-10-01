"""Batch25 saved-request audit, never sends requests or changes course code.

Exact equality is on public prompt characters. Token positions are approximate:
estimate_tokens(prefix)/estimate_tokens(prompt), scaled to reported all-input.
The current V4 docs don't publish checkpoint intervals. 64 is legacy sensitivity,
not a guaranteed block size. Endpoint persistence is an explanatory best-effort
simulation (complete inputs and common prefixes detected on two earlier calls).
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import argparse
import json
from pathlib import Path
import re

from refactor.learning.connections.pricing import estimate_tokens
from .audit import canonical,lcp
from .acceptance_audit import PURPOSES,load_calls,effort

LABEL=re.compile(r'(?m)^【([^\n】]+)】\n')

def sections(content):
    matches=list(LABEL.finditer(content))
    if not matches or matches[0].start()!=0:return [content]
    return [content[m.start():matches[i+1].start() if i+1<len(matches) else len(content)] for i,m in enumerate(matches)]

def field_order(block,keys):
    label,sep,text=block.partition('\n')
    try:value=json.loads(text)
    except ValueError:return block
    if not isinstance(value,dict):return block
    ordered={k:value[k] for k in (*keys,*value) if k in value}
    return label+sep+json.dumps(ordered,ensure_ascii=False,separators=(',',':'))

def proposed(row):
    """A proposal only: move existing data blocks/keys; no added module sources.

    Teacher source before dialogue; task profile before pending question/user.
    Profile preference first within task. Judge constant mode before point IDs.
    Question requirements constants before avoid/used_angles/target-specific data.
    All scalar text and array order retained. Does not claim to solve subset changes.
    """
    row=deepcopy(row)
    blocks=[dict(m,content=b) for m in row['messages'] for b in sections(m['content'])]
    if row['purpose']=='teacher':
        source=[m for m in blocks if m['content'].startswith('【当前模块原文（数据）】')]
        if source:
            others=[m for m in blocks if m not in source]
            blocks=others[:1]+source+others[1:]
        blocks[-1]['content']=field_order(blocks[-1]['content'],('profile','answer_boundary','director','pending_question','user'))
    elif row['purpose']=='profile':
        blocks[-1]['content']=field_order(blocks[-1]['content'],('preferences','mode','current_point_id','profile','progress','weak_point_ids'))
    elif row['purpose']=='judge':
        # Move this existing task object (mode first) before variable point list.
        blocks.sort(key=lambda m:0 if m['content'].startswith('【本次判定】') else 1)
    elif row['purpose']=='questions':
        blocks=[dict(m,content=field_order(m['content'],('purpose','per_point','mode','count','avoid','used_angles'))) for m in blocks]
    row['messages']=blocks
    return row

def info_signature(row):
    def value(block):
        title,sep,text=block.partition('\n')
        try:data=json.loads(text)
        except ValueError:data=text
        return json.dumps([title,data],ensure_ascii=False,sort_keys=True)
    return row['system'],sorted((m['role'],value(b)) for m in row['messages'] for b in sections(m['content']))

def known(row):
    u=row.get('usage') or {};missing=u.get('raw',{}).get('_mentorflow',{}).get('unreported_fields',[])
    return bool(u.get('input_tokens') and not {'input_tokens','cached_input_tokens'}&set(missing))

def audit(rows,block=1):
    history=defaultdict(list);observations=[]
    for r in rows:
        text=canonical(r);compatible=[p for p in history[r['purpose']] if effort(p[0])==effort(r)]
        prev=compatible[-1] if compatible else None
        n=lcp(text,prev[1]) if prev else 0
        best=max((lcp(text,t) for _,t in compatible),default=0)
        endpoints=[len(t) for _,t in compatible if text.startswith(t)]
        for i,(_,a) in enumerate(compatible):
            for _,b in compatible[:i]:
                common=lcp(a,b)
                if text.startswith(a[:common]):endpoints.append(common)
        end=max(endpoints,default=0)
        unit=estimate_tokens(text)
        def fraction(chars):return min(1,(estimate_tokens(text[:chars])//block*block)/unit) if unit else 0
        measured=known(r);u=r.get('usage') or {};total=u['input_tokens'] if measured else None;hit=u['cached_input_tokens'] if measured else None
        theory=total*fraction(n) if measured else None
        label=text[:n].rpartition('【')[2].partition('】')[0]
        cause=('first' if not prev else 'system_variant' if r['system']!=prev[0]['system'] else
               'source_subset_or_module' if '原文' in label or '资料' in label else
               'dialogue_append_or_window' if '对话' in label else
               'point_list_or_module' if '知识点' in label or '模块' in label else 'task_change')
        loss=max(0,total-hit) if measured else None
        observations.append(dict(id=r['id'],purpose=r['purpose'],previous=prev[0]['id'] if prev else None,
            cause=cause,boundary=label,prefix_chars=n,prompt_chars=len(text),mismatch=text[n:n+80],
            measured=measured,input_tokens=total,cached_tokens=hit,
            adjacent_est=theory,best_prior_est=total*fraction(best) if measured else None,
            endpoint_est=total*fraction(end) if measured else None,
            actual_miss=loss,structure_attributed_est=min(loss,total-theory) if measured else None,
            unexplained_est=max(0,theory-hit) if measured else None,
            gap_seconds=(datetime.fromisoformat(r['at'])-datetime.fromisoformat(prev[0]['at'])).total_seconds() if prev else None))
        history[r['purpose']].append((r,text))
    def aggregate(items):
        good=[r for r in items if r['measured']];total=sum(r['input_tokens'] for r in good)
        return dict(calls=len(items),known=len(good),missing=len(items)-len(good),input_tokens=total,
            **{f'{k}_tokens':sum(r[k] for r in good) for k in ('actual_miss','structure_attributed_est','unexplained_est')},
            **{f'{k}_rate':sum(r[k] for r in good)/total if total else None for k in ('cached_tokens','adjacent_est','best_prior_est','endpoint_est')})
    groups=defaultdict(list);causes=defaultdict(list)
    for r in observations:groups[r['purpose']].append(r);causes[r['cause']].append(r)
    return dict(block_sensitivity=block,total=aggregate(observations),warm=aggregate([r for r in observations if r['previous']]),
        purposes={p:dict(all=aggregate(v),warm=aggregate([r for r in v if r['previous']])) for p,v in groups.items()},
        causes={p:aggregate(v) for p,v in causes.items()},requests=observations)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--calls',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();saved=load_calls(args.calls)
    missing=[r['id'] for r in saved if 'messages' not in r or 'system' not in r]
    rows=[r for r in saved if r['id'] not in missing];changed=[proposed(r) for r in rows]
    assert all(info_signature(a)==info_signature(b) for a,b in zip(rows,changed))
    before,candidate=audit(rows),audit(changed)
    accepted=[p for p in before['purposes'] if
        (candidate['purposes'][p]['warm']['adjacent_est_rate'] or 0)>(before['purposes'][p]['warm']['adjacent_est_rate'] or 0)
        and (candidate['purposes'][p]['warm']['best_prior_est_rate'] or 0)>=(before['purposes'][p]['warm']['best_prior_est_rate'] or 0)]
    selected=[proposed(r) if r['purpose'] in accepted else r for r in rows]
    data=dict(missing_request_ids=missing,basis='Exact characters; approximate token-scaled positions. AFTER counters are historical, not new measurements.',
        accepted_purposes=accepted,before=before,candidate=candidate,proposal=audit(selected),legacy64=audit(rows,64),proposal64=audit(selected,64))
    args.out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:data[k]['warm'] for k in ('before','proposal','legacy64','proposal64')}))

if __name__=='__main__':main()
