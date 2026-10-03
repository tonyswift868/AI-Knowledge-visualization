"""Offline agents only. Classroom, material selection, bank and store stay real."""
import json
import re
import threading
import time
from types import SimpleNamespace


def block(messages, label):
    text = '\n'.join(m['content'] for m in messages).split(label + '\n', 1)[1]
    return json.JSONDecoder().raw_decode(text)[0]


def bank_question(pid, mid, *, index=0, kind='short', ids=None, answer='先进先出', chunk='k1'):
    source = dict(title='官方原文', url='https://docs.python.org/3/tutorial/', locator=chunk)
    row = dict(id=f'{pid}-{index}', module_id=mid, point_ids=ids or [pid], kind=kind,
        stem=f'请说明{pid}的含义（{index}）？', answer=answer, explanation='', origin='found',
        source=source, answer_source=source, importance='key', uses=[], created_at='2026-10-03T00:00:00+00:00')
    if kind in ('choice', 'multi'):
        row.update(options=['先进先出', '后进先出', '没有顺序'], answer=[0])
    if kind == 'fill': row.update(stem=f'{pid}按____处理元素（{index}）。', answer=[[answer]])
    return row


def seed_bank(store, plan_id, *, count=3):
    """Stored bank plus batch42 quick fixtures while MF02's role schema is unmerged."""
    for p in store.records('points', plan_id):
        store.add_bank_questions(plan_id, p['module_id'], [bank_question(p['id'], p['module_id'], index=i,
            answer=store.records('source_chunks', plan_id)[0]['text'], chunk=p['chunk_ids'][0]) for i in range(count)])
    attach_quick_contract(store, plan_id)


def attach_quick_contract(store, plan_id):
    """Add the unmerged quick contract without changing persisted bank questions."""
    quick=[]
    for p in store.records('points', plan_id):
        chunk=next(c for c in store.records('source_chunks',plan_id) if c['id']==p['chunk_ids'][0])
        word=chunk['text'][:2]
        q=bank_question(p['id'],p['module_id'],kind='choice',chunk=chunk['id'])
        q.update(id=p['id']+'-quick42',role='quick',origin='cloze',stem=chunk['text'].replace(word,'____',1),
                 options=[word,'另一概念','无关说法'],answer=[0],explanation=chunk['text'])
        quick.append(q)
    store._offline_quick42=quick
    records=store.records
    def projected(entity, plan_key=''):
        rows=records(entity,plan_key)
        if entity=='question_bank' and plan_key==plan_id:
            return [dict(q,role='bank') for q in rows]+[dict(q) for q in quick]
        return rows
    store.records=projected


class Agents:
    def __init__(self):
        self.calls = []; self.requests = []; self.verdicts = []
        self.teacher_format_errors = 0; self.lock = threading.Lock()
        self.choice = False; self.choice_bank_points = set()

    def complete(self, purpose, messages, *, system=None, on_text=None, **kwargs):
        with self.lock:
            self.calls.append(purpose)
            self.requests.append(dict(purpose=purpose, system=system, messages=messages))
        if purpose == 'teacher':
            task = block(messages, '【本步任务】')
            if self.teacher_format_errors:
                self.teacher_format_errors -= 1
                text = '无合法引用的格式错误'
            else:
                text = ('这一章一起理解资料中的概念，你觉得哪种情况更接近？[c1]\n【选项】\n第一种\n第二种\n不知道\n【/选项】'
                        if task['opening'] else '我们一起看资料中的定义。[c1]')
            if on_text: on_text(text)
        elif purpose == 'judge':
            task = block(messages, '【本次判定】')
            points = block(messages, '【知识点清单】')
            kind, correct, wrong = self.verdicts.pop(0) if self.verdicts else ('answer', True, [])
            value = dict(kind=kind, correct=correct, wrong_points=wrong, reason='回答符合原文。' if correct else '回答不符合原文。')
            if task['question'].get('kind', task['question'].get('type')) in ('short', 'essay', 'calc', 'fix_error'):
                value.update(uncertain=False, checks=[dict(point_id=points[min(i,len(points)-1)]['id'], requirement=req,
                    applies=True, met=points[min(i,len(points)-1)]['id'] not in wrong,
                    evidence=task['reply'] if correct else '') for i,req in enumerate(task['required_points'])]
                    if kind in ('answer', 'insufficient') else [])
            text = json.dumps(value, ensure_ascii=False)
        elif purpose == 'profile':
            task = block(messages, '【本次档案更新】')
            text = json.dumps(dict(progress=task['progress'], preferences=task['preferences'], last_stop='本模块结束。',
                weak_points=[dict(point_id=p, note='具体困难尚待确认') for p in task['weak_point_ids']]), ensure_ascii=False)
        elif purpose == 'check_profile':
            text = json.dumps(dict(categories=[], teacher_profile='没有足够的错误证据。'), ensure_ascii=False)
        else:
            raise AssertionError('unexpected or removed purpose: '+purpose)
        return SimpleNamespace(text=text, search_hits=(), citations=())


def context(streamed=None):
    return SimpleNamespace(cancel=threading.Event(), stream=streamed.append if streamed is not None else lambda _: None)


def operate(room, plan_id, name, *args, streamed=None):
    return room.perform(plan_id, name, context(streamed), args)


def to_question(room, plan_id, view, *, streamed=None):
    """Clock/accounting fixtures advance non-scored conversation explicitly."""
    for _ in range(5):
        if view.get('failure'): raise AssertionError(view['failure'])
        if view['step'] == 'closing':
            view = operate(room,plan_id,'next',streamed=streamed)
            today = view.get('today') or {}
            if view['step']=='teach' and today.get('today_target',0)>0 and today.get('today_confirmed',0)+today.get('today_tentative',0)>=today['today_target']:
                return view
        elif view['step'] == 'teach': view = operate(room,plan_id,'next',streamed=streamed)
        elif view['step'] == 'opening': view = operate(room,plan_id,'choose',0,streamed=streamed)
        elif view['step'] == 'teaching': view = operate(room,plan_id,'next',streamed=streamed)
        else: return view
    raise AssertionError('conversation did not reach a question')


def join_workers():
    for worker in list(threading.enumerate()):
        if worker.name.startswith(('classroom-', 'plan-materials')):
            worker.join(5)
            assert not worker.is_alive()
