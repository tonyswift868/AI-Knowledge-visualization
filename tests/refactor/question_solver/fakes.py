"""Explicit synthetic solver/repair responses for wiring tests, never quality claims."""
import json
from types import SimpleNamespace


def response(purpose, messages):
    if purpose == 'question_solve':
        q = json.loads(messages[-1]['content'].split('\n', 1)[1])
        index = 1 if q['type']=='choice' and q['options'][0].startswith('甲') and q['options'][1].startswith('乙') else 0
        return SimpleNamespace(text=json.dumps(dict(answerable=True,
            answer=q['options'][index] if q['type']=='choice' else '3' if q['type']=='exact' else '按原文执行操作，在原列表末尾追加，返回 None',
            viable_options=[index] if q['type']=='choice' else [], reason='仅验证接线的合成作答。'),ensure_ascii=False))
    if purpose == 'questions':
        text=messages[-1]['content']
        task=json.loads(text.split('【本次出题要求】\n')[1])
        if not task.get('written_only'): return None
        points=json.JSONDecoder().raw_decode(text.split('【知识点清单】\n')[1])[0]
        questions=[]
        for p in points:
            angles=[a for a in ('recall','apply','contrast','fix_error','transfer') if a not in task['used_angles'][p['id']]]
            for i in range(task['counts'][p['id']]):
                questions.append(dict(id=f"repair-{p['id']}-{angles[i]}",type='short',
                    stem=f"补题{p['id']}角度{angles[i]}：请说明资料所述操作怎样执行。",options=[],answer='按原文说明的方法执行操作',
                    reference='依据原文说明的操作。',point_ids=[p['id']],chunk_ids=p['chunk_ids'][:1],angle=angles[i],
                    required_points=[dict(point='说明操作怎样执行',accept=['按原文执行操作'],reject=[])]))
        return SimpleNamespace(text=json.dumps(dict(questions=questions),ensure_ascii=False))
    return None


def grade(messages):
    task=json.loads(messages[-1]['content'].split('【本次判定】\n')[1])
    q=task['question']
    result=dict(kind='answer',correct=True,wrong_points=[],reason='合成作答满足所问要点。')
    if q['type'] in ('short','fix_error'):
        checks=[]
        for item in q['required_points']:
            if isinstance(item,dict):
                checks.append(dict(point_id=q['point_ids'][0],point=item['point'],applies=True,status='hit',evidence=task['reply']))
            else:
                checks.append(dict(point_id=q['point_ids'][0],requirement=item,applies=True,met=True,evidence=task['reply']))
        result.update(uncertain=False,checks=checks)
    return SimpleNamespace(text=json.dumps(result,ensure_ascii=False))


def is_solver_judgement(messages):
    """Distinguish synthetic blind answers from scripted student verdict queues."""
    if '【本次判定】\n' not in messages[-1]['content']: return False
    task=json.loads(messages[-1]['content'].split('【本次判定】\n')[1])
    return task['reply']=='按原文执行操作，在原列表末尾追加，返回 None'


def bank_response(purpose, messages, **kwargs):
    extra=response(purpose, messages)
    if extra is not None: return extra
    if purpose=='judge': return grade(messages)
    assert purpose=='question_review'
    return SimpleNamespace(text='{"verdict":"approve","issues":[],"revised":null}')
