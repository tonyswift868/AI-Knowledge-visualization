"""Objective replacements for old scripted classroom fixtures, not product data."""
import copy


def objective_bank(store):
    if getattr(store,'_objective44',False):return
    records=store.records
    def read(entity,plan=''):
        rows=records(entity,plan)
        if entity!='question_bank':return rows
        result=copy.deepcopy(rows)
        for q in result:
            if q['kind'] in ('short','essay'):
                q.update(kind='choice',options=[q['answer'],'与资料相反的错误说法'],answer=[0])
        return result
    store.records=read
    store._objective44=True


def scripted_answer(room,plan,agents,text):
    pending=room._file(plan).read({}).get('pending')
    if not pending:return 'reply',(text,)
    q=pending['question']
    if q['kind'] not in ('choice','multi','fill','calc'):return 'reply',(text,)
    kind,good,wrong=agents.verdicts.pop(0) if agents.verdicts else ('answer',True,[])
    if kind!='answer':raise AssertionError('obsolete AI grading scenario')
    if q['kind']=='fill':
        # 44-MF02 made shared fixtures literal fill terms; answer them from the stored row.
        return 'answer',([group[0] if good else '与原文不符' for group in q['answer']],)
    if q['kind']=='calc':
        return 'answer',(q['answer'] if good else '-987654321',)
    value=q['answer'] if good else [next(i for i in range(len(q['options'])) if i not in q['answer'])]
    return 'answer',(value,)
