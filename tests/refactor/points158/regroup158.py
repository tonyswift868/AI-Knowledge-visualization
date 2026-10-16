"""Opt-in thematic regrouping proposal, never writes the learner's database.

python -B -m tests.refactor.points158.regroup158 --module 5 --live
The model only groups short aliases and proposes titles/goals. Python copies
the original subpoint payloads, including stable IDs, notes and minutes.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

ROOT=Path('D:/MentorFlow/.local/test-tmp/points158')
ACCOUNT='mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'
SYSTEM='''你是课程主题编排者。把已有小要点按教学主题重新组织为大知识点，仅分组、命名、写掌握目标，不改内容。
用户要连贯：同一主题及互相依赖的前因后果放一起，先背景与原理再实现与应用，不能突然冒出无上下文的细节。
大小完全由主题决定，没有分钟、组数或小要点数的上下限，不按字数切、不凑平均、不按固定分钟拆。一个主题可以含几十个小要点。
同主题分散在原文不同位置时可以归一起；不同的原文讲法和重叠仍保留，不删、不合并小要点。总结、小结、选型建议归到相应主题末部，不单独建泛泛“总结”主题。
拿到的是整个模块，protected=true 的小要点已学/当前在学，实际换入会保留原父点并从提案组剔除它们。分组仍覆盖全部输入；掌握目标不要假定被保护部分会再学。
大点之间按学习依赖排序；组内程序会恢复原文顺序。每个S编号必须恰好出现一次，不能创造编号。
大点标题用简短中文主题名，可保留技术名词，如“分布式锁：Redis 实现”；不要序号串、不要用整段第一句话、不要标题拼盘。
修坏的小标题：截断英语/半句、表格碎片、代码片段、Markdown标记、序号开头和无上下文“总结”。根据其讲义用简短主题标题，只改标题。
原本清楚的标题不必改。renames只列需要修改的小要点，理由一句话；不要写讲义、分钟、出处、题或任何新知识。
仅返回JSON对象：{"groups":[{"title":"主题名","goal":"掌握目标","subpoint_ids":["S1","S2"],"reason":"为什么放一起"}],"renames":[{"id":"S1","title":"新小标题","reason":"原名为何不清楚"}],"ordering_reason":"大点学习顺序的理由"}。'''


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def catalog(module,snapshot):
    protected=set(snapshot.get('protected_ids',snapshot.get('protection',{})))
    chunks=snapshot.get('chunk_map') or {c['id']:c for c in snapshot['chunks']}
    aliases={};rows=[]
    for p in sorted(module.get('points',module.get('old_points',[])),key=lambda v:(v['position'],v['id'])):
        for sub in p['subpoints']:
            alias='S'+str(len(aliases)+1);aliases[alias]=(p,sub)
            sections=list(dict.fromkeys(' / '.join(chunks[c].get('section',[])) for c in sub['chunk_ids'] if c in chunks))
            rows.append(dict(id=alias,title=sub['title'],old_point=p['title'],protected=p['id'] in protected,
                minutes=sub['minutes'],sections=sections,notes=[n['text'] for n in sub.get('notes',[])]))
    return aliases,rows


def parse(text,aliases):
    text=text.strip()
    if text.startswith('```'):
        text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
    value=json.loads(text)
    if not isinstance(value,dict) or not isinstance(value.get('groups'),list) or not value['groups']:
        raise ValueError('groups必须为非空列表')
    sequence=[]
    for group in value['groups']:
        if not isinstance(group,dict) or not all(isinstance(group.get(k),str) and group[k].strip() for k in ('title','goal','reason')):
            raise ValueError('每个主题须有title/goal/reason')
        if not isinstance(group.get('subpoint_ids'),list) or not group['subpoint_ids'] or any(not isinstance(s,str) for s in group['subpoint_ids']):
            raise ValueError('主题须有小要点短编号列表')
        sequence+=group['subpoint_ids']
    counter=Counter(sequence);issues=dict(missing=sorted(set(aliases)-set(sequence)),unknown=sorted(set(sequence)-set(aliases)),duplicate=sorted(k for k,v in counter.items() if v!=1))
    if any(issues.values()):raise ValueError('覆盖不一致：'+json.dumps(issues,ensure_ascii=False))
    seen=set()
    for row in value.get('renames',[]):
        if not isinstance(row,dict) or row.get('id') not in aliases or row['id'] in seen or not isinstance(row.get('title'),str) or not row['title'].strip():
            raise ValueError('renames编号/标题无效或重复')
        seen.add(row['id'])
    return value


def export_module(module,snapshot,value):
    mid=module['module_id']
    aliases,_=catalog(module,snapshot)
    renames={r['id']:r['title'].strip() for r in value.get('renames',[])}
    rank={s:i for i,s in enumerate(aliases)}
    protected=set(snapshot.get('protected_ids',snapshot.get('protection',{})))
    points=[];audit=[]
    for index,group in enumerate(value['groups']):
        subs=[]
        for alias in sorted(group['subpoint_ids'],key=rank.__getitem__):
            old,original=aliases[alias];sub=deepcopy(original)
            if alias in renames and old['id'] not in protected:sub['title']=renames[alias]
            subs.append(sub)
            if sub['title']!=original['title']:audit.append(dict(id=sub['id'],old=original['title'],new=sub['title']))
        identity=mid+'|'+group['title']+'|'+','.join(s['id'] for s in subs)
        pid=mid+'-theme-'+hashlib.sha256(identity.encode()).hexdigest()[:16]
        points.append(dict(id=pid,module_id=mid,position=index,title=group['title'].strip(),summary=group['goal'].strip(),
            goal=group['goal'].strip(),structure='concept_v1',minutes=sum(s['minutes'] for s in subs),
            chunk_ids=list(dict.fromkeys(c for s in subs for c in s['chunk_ids'])),subpoints=subs))
    original=module.get('points',module.get('old_points',[]))
    result=dict(module_id=mid,replace_point_ids=[p['id'] for p in original],points=points,
        navigation=deepcopy(module.get('navigation',[])))
    return result,audit


def build(snapshot,root=ROOT):
    modules=[];audit=[];reviews=[]
    for m in snapshot['modules']:
        file=root/f'group-{m["position"]}.json'
        if not file.exists():continue
        aliases,_=catalog(m,snapshot);value=parse(file.read_text(encoding='utf-8'),aliases)
        result,changes=export_module(m,snapshot,value);modules.append(result);audit.extend(changes)
        reviews.append(dict(module_id=m['module_id'],title=m['title'],ordering_reason=value.get('ordering_reason'),groups=[dict(title=g['title'],reason=g['reason'],members=len(g['subpoint_ids'])) for g in value['groups']]))
    # Having three valid JSON files is coverage, not a thematic review. A
    # reviewer explicitly approves the final artifact after checking its notes.
    output=dict(plan_id=snapshot['plan_id'],complete=len(modules)==3,regroup_subpoints=True,
        quality_review=dict(passed=False,reason='待逐模块人工主题复核与自动保全校验'),modules=modules)
    dump(root/'apply-ready.json',output);dump(root/'renamed-titles.json',audit);dump(root/'grouping-reasons.json',reviews)
    return output


def run(module,snapshot,*,live=False,account=ACCOUNT,preset_root=Path('D:/MentorFlowData/account-shell'),root=ROOT):
    aliases,rows=catalog(module,snapshot)
    request=dict(module_title=module['title'],subpoints=rows)
    messages=[dict(role='user',content=json.dumps(request,ensure_ascii=False))]
    dump(root/f'prompt-{module["position"]}.json',dict(system=SYSTEM,messages=messages))
    if not live:return dict(subpoints=len(aliases),input_bytes=len((SYSTEM+messages[0]['content']).encode()))
    from tests.refactor.simulation.run import live_factory
    from tests.refactor.simulation.engine import Evidence,RecordedGateway
    from refactor.learning.connections.gateway import MODEL_MAX_OUTPUT,PURPOSE_MIN_OUTPUT
    evidence=Evidence(root/'model-audit')
    real=live_factory(preset_root,account)(SimpleNamespace(account_id=account,data_root=root/'model-config'))
    gateway=RecordedGateway(real,evidence,budget='100',allowed={'generate'})
    try:
        config=real.get_configuration();cap=MODEL_MAX_OUTPUT.get(config['model'],max(32768,PURPOSE_MIN_OUTPUT.get('generate',0)))
        for attempt in range(2):
            upper=len((SYSTEM+json.dumps(messages,ensure_ascii=False)).encode())+4096
            estimate=real.price_catalog.estimate_cost(config['provider_id'],config['model'],input_tokens=(upper,upper),
                output_tokens=(cap,cap),cache_hit_rate=(0,0),context=dict(real.estimate_context(),price_period='peak',cache_mode='implicit'))
            if estimate is None or gateway.spent+estimate.high>Decimal('100'):
                raise RuntimeError('本次最高费用预留超过¥100总上限，未发送')
            with (root/'report.md').open('a',encoding='utf-8') as f:
                f.write(f'\n\n付费前预期：模块position={module["position"]}，{len(aliases)}个小要点；约输入{upper//3}～{upper} token，预计输出5千～1.5万token（仅估计，不截断）；调用现有generate/Gateway，实际模型cap={cap}，本次最高预留¥{estimate.high}；已有费用¥{gateway.spent}。预计返回完整主题分组、坏标题更名和顺序理由，不改讲义/出处/分钟/题。\n')
            try:
                # Public Gateway accepts <=32768 and applies its published-model
                # maximum internally. Reserve the ACTUAL cap above, not this arg.
                response=gateway.complete('generate',messages,system=SYSTEM,cancel=threading.Event(),max_tokens=32768,plan_id=snapshot['plan_id'],on_text=lambda _:None)
            finally:
                if evidence.calls:
                    record=evidence.calls[-1];file=evidence.root/record['evidence']
                    detail=json.loads(file.read_text(encoding='utf-8'))
                    detail['unknown_usage_bound_cny']=str(estimate.high)
                    record['unknown_usage_bound_cny']=str(estimate.high)
                    meta=detail.get('usage',{}).get('raw',{}).get('_mentorflow',{}) if isinstance(detail.get('usage'),dict) else {}
                    unreported=set(meta.get('unreported_fields',[]))
                    if detail.get('error') and ('usage' not in detail or {'input_tokens','output_tokens'} & unreported):
                        original=Decimal(record['charged_bound']);charge=estimate.high
                        if detail['error']['code'] in ('MODEL_CONFIG_INVALID','MODEL_KEY_REQUIRED','MODEL_NOT_CONFIGURED'):
                            charge=Decimal(0);detail['network_not_started']=True
                        detail['charged_bound']=record['charged_bound']=str(charge)
                        gateway.spent+=charge-original
                    evidence.save(record['evidence'],detail);evidence.save('calls.json',evidence.calls)
            try:value=parse(response.text,aliases)
            except (ValueError,TypeError,KeyError) as exc:
                dump(root/f'invalid-group-{module["position"]}-{attempt}.json',dict(text=response.text,error=str(exc)))
                if attempt:raise
                messages+=[dict(role='assistant',content=response.text),dict(role='user',content='请修复格式/覆盖错误，完整重发全部JSON，不删任何小要点：'+str(exc))]
                continue
            dump(root/f'group-{module["position"]}.json',value)
            return dict(groups=len(value['groups']),renames=len(value.get('renames',[])),peak_cost=str(gateway.spent))
    finally:gateway.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--module',type=int,choices=[5,6,7],required=True)
    parser.add_argument('--live',action='store_true');parser.add_argument('--account-id',default=ACCOUNT)
    parser.add_argument('--preset-root',type=Path,default=Path('D:/MentorFlowData/account-shell'));args=parser.parse_args()
    snapshot=json.loads((ROOT/'snapshot.json').read_text(encoding='utf-8'));module=next(m for m in snapshot['modules'] if m['position']==args.module)
    result=run(module,snapshot,live=args.live,account=args.account_id,preset_root=args.preset_root)
    if args.live:build(snapshot)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
