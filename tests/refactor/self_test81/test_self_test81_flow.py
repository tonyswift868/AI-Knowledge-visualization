"""Offline literal locators, real persistence and grading; no model/network."""
import copy
import io
import json
import random
from threading import Event
from types import SimpleNamespace

import pytest

from refactor.learning.course.self_test import SelfTestStore, compile_row, extract_questions, register
from refactor.learning.course.extract import extract_bytes
from refactor.learning.connections.errors import ModelCancelled


OBJECTIVE = '1. 哪个数是偶数？\nA. 2\nB. 3\n答案：A'
RECALL = '# 什么是队列？\n\n队列遵循先进先出。元素从队尾加入，从队首移除。'
MISSING = '3. 计算 2+3 的结果？'


class Locator:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def complete(self, purpose, messages, **kwargs):
        self.calls.append((purpose, json.loads(messages[0]['content']), kwargs))
        return SimpleNamespace(text=json.dumps(dict(questions=self.rows), ensure_ascii=False))


def rows():
    return [dict(kind='objective', objective_kind='choice', stem='1. 哪个数是偶数？', options=['A. 2', 'B. 3'], answer='A', source={'quote':'1. 哪个数是偶数？','end_quote':'答案：A'}),
            dict(kind='recall', objective_kind=None, stem='什么是队列？', options=[], answer='队列遵循先进先出。元素从队尾加入，从队首移除。', source={'quote':'什么是队列？','end_quote':'元素从队尾加入，从队首移除。'}),
            dict(kind='objective', objective_kind='calc', stem=MISSING, options=[], answer=None, source=MISSING)]


def fixture():
    return extract_questions(extract_bytes((OBJECTIVE+'\n\n'+RECALL+'\n\n'+MISSING).encode(), '练习.md'), Locator(rows()))


def test_self_test81_import_counts_missing_literal_and_existing_recognition():
    gateway = Locator(rows())
    result = extract_questions(extract_bytes((OBJECTIVE+'\n\n'+RECALL+'\n\n'+MISSING).encode(), '练习.md'), gateway)
    assert [q['kind'] for q in result['questions']] == ['objective', 'recall']
    assert len(result['missing']) == 1 and result['missing'][0]['stem'] == MISSING
    assert not result['rejected']
    assert result['questions'][0]['grading']['answer'] == [0]
    assert result['questions'][1]['key_points'] == ['队列遵循先进先出。元素从队尾加入，从队首移除。']
    assert gateway.calls[0][1]['recognized']
    assert any(row['kind']=='recall' for row in gateway.calls[0][1]['recognized'])
    assert gateway.calls[0][2]['enable_search'] is False
    assert len(gateway.calls) == 1  # Meaning grouping does not call a selector.


@pytest.mark.parametrize('field,value', [('stem','改写的题干'), ('answer','人工补出的答案'), ('options',['A. 改写','B. 3']), ('source','不存在的原文')])
def test_self_test81_rewritten_fields_never_admitted(field, value):
    raw = dict(rows()[0], **{field: value})
    result = extract_questions(extract_bytes(OBJECTIVE.encode(), '题.md'), Locator([raw]))
    assert not result['questions'] and result['rejected']


def test_self_test81_answer_cannot_be_borrowed_from_another_question():
    raw = dict(rows()[0], answer='B')
    with pytest.raises(ValueError):
        compile_row(raw, OBJECTIVE+'\n\n另一道题的答案：B', Locator([]))


def test_self_test81_literal_range_pointers_and_long_answer_units():
    reference = '一号结论。二号结论。三号结论。四号结论。五号结论。六号结论。'
    span = '# 六条结论是什么？\n\n'+reference
    class Pick:
        def complete(self, *args, **kwargs):
            pytest.fail('Literal paragraph grouping needs no selector')
    raw = dict(kind='recall', stem='六条结论是什么？', options=[], answer={'quote':'一号结论。','end_quote':'六号结论。'},
               source={'quote':'# 六条结论是什么？','end_quote':'六号结论。'})
    q = compile_row(raw, span, Pick())
    assert q['reference'] == reference and q['key_points'] == [reference]
    q = compile_row(raw, span, Locator([]))
    assert q['key_points'] == [reference] and not q.get('warning')


@pytest.mark.parametrize('filename,data', [
    ('题.txt', OBJECTIVE.encode()), ('题.md', OBJECTIVE.encode()),
    ('题.html', ('<p>'+OBJECTIVE.replace('\n','</p><p>')+'</p>').encode()),
    ('题.csv', ('题干,选项,答案\n"'+OBJECTIVE.replace('"','""')+'",,\n').encode()),
])
def test_self_test81_uses_existing_text_extractors(filename, data):
    doc = extract_bytes(data, filename)
    original = '\n\n'.join(b.text for b in doc.blocks)
    # Locate within whatever the real importer yields; never bypass extraction.
    raw = rows()[0]; raw['source'] = original
    result = extract_questions(doc, Locator([raw]))
    assert len(result['questions']) == 1


def test_self_test81_word_and_pdf_extractors():
    from docx import Document
    import importlib.util
    from pathlib import Path
    # Reuse the existing dependency-free Unicode PDF fixture generator.
    spec=importlib.util.spec_from_file_location('pdf_fixture81',Path(__file__).parents[1]/'extract/test_extract.py')
    pdf_fixture=importlib.util.module_from_spec(spec); spec.loader.exec_module(pdf_fixture)
    word = Document()
    for line in OBJECTIVE.splitlines():
        word.add_paragraph(line)
    buffer = io.BytesIO(); word.save(buffer)
    doc = extract_bytes(buffer.getvalue(), '题.docx')
    original = '\n\n'.join(b.text for b in doc.blocks)
    assert extract_questions(doc, Locator([dict(rows()[0], source=original)]))['questions']
    doc = extract_bytes(pdf_fixture.pdf_bytes(['1. What is 2+3? Answer: 5. This is an original exercise with an answer.']), '题.pdf')
    original = '\n\n'.join(b.text for b in doc.blocks)
    result = extract_questions(doc, Locator([dict(kind='objective', objective_kind='calc', stem='1. What is 2+3?', options=[], answer='5', source=original)]))
    assert result['questions'][0]['answer'] == '5'


@pytest.mark.parametrize('kind,stem,answer,reply', [
    ('calc','1. 计算 2+3','5','5'), ('fill','1. 队列按____处理','先进先出',['先进先出']),
    ('fill','1. ____和____分别为两种操作','入队；出队',['入队','出队']),
    ('multi','1. 哪些是偶数？','AC',[0,2]),
])
def test_self_test81_objective_grades_with_existing_compare(tmp_path, kind, stem, answer, reply):
    options = ['A. 2','B. 3','C. 4'] if kind == 'multi' else []
    span = stem+'\n'+'\n'.join(options)+'\n答案：'+answer
    q = compile_row(dict(kind='objective',objective_kind=kind,stem=stem,options=options,answer=answer,source=span), span, Locator([]))
    store = SelfTestStore('account',tmp_path)
    body = store.create('客观',dict(questions=[q],missing=[],rejected=[],warnings=[]))
    view = store.start(body['id'])
    result = store.submit(body['id'],view['run']['current']['attempt_id'],answer=reply)
    assert result['run']['current']['verdict']['correct']


def bank(count=4):
    q = fixture()['questions'][0]
    return dict(questions=[dict(copy.deepcopy(q), id=str(i), stem=f'题目 {i}') for i in range(count)],missing=[],rejected=[],warnings=[])


@pytest.mark.parametrize('required,count', [(2,1),(3,1),(2,2),(3,2),(2,3),(3,4),(2,9)])
def test_self_test81_streaks_shuffle_gap_and_complete(tmp_path, required, count):
    store = SelfTestStore('account',tmp_path,rng=random.Random(7))
    body = store.create('练习',bank(count))
    view = store.start(body['id'],required)
    initial = store.get(body['id'])['run']['queue']
    if count > 2:
        assert initial != [str(i) for i in range(count)]
    history = []
    for _ in range(100):
        if view['run']['completed']: break
        qid = view['question']['id']; current = view['run']['current']
        assert qid not in history[-min(2,count-1):] if count > 1 else True
        history.append(qid)
        store.submit(body['id'],current['attempt_id'],answer=[0])
        view = store.next(body['id'],current['attempt_id'])
    assert view['run']['completed'] and view['passed'] == count
    assert all(result['streak'] >= required and result['wrong'] == 0 for result in view['results'])


def test_self_test81_wrong_resets_retry_idempotent_and_resume_exact_attempt(tmp_path):
    now = [10.0]
    store = SelfTestStore('account',tmp_path,clock=lambda:now[0])
    body = store.create('练习',bank(1)); key=body['id']
    view = store.start(key,3)
    attempt=view['run']['current']['attempt_id']
    now[0]+=7
    store.submit(key,attempt,answer=[0]); store.submit(key,attempt,answer=[0])
    assert store.get(key)['run']['stats']['0']['correct']==1
    view=store.next(key,attempt); attempt=view['run']['current']['attempt_id']
    result=store.submit(key,attempt,answer=[1])
    assert result['run']['stats']['0']['streak']==0
    assert result['run']['stats']['0']['wrong']==1
    store.pause(key); now[0]+=600
    reopened=SelfTestStore('account',tmp_path,clock=lambda:now[0])
    result=reopened.resume(key)
    assert result['run']['current']['attempt_id']==attempt and result['run']['current']['verdict']['correct'] is False
    assert result['run']['elapsed_seconds']==7
    with pytest.raises(ValueError): reopened.submit(key,'stale',answer=[0])


def test_self_test81_recall_requires_answer_then_reveal_all_points(tmp_path):
    store = SelfTestStore('account',tmp_path)
    q=fixture()['questions'][1]
    key=store.create('问答',dict(questions=[q],missing=[],rejected=[],warnings=[]))['id']
    view=store.start(key)
    assert 'reference' not in view['question']
    attempt=view['run']['current']['attempt_id']
    with pytest.raises(ValueError): store.submit(key,attempt,ticked=[0,1])
    with pytest.raises(ValueError): store.reveal(key,attempt,'  ')
    store.reveal(key,attempt,'先进先出')
    reopened=SelfTestStore('account',tmp_path)
    assert reopened.resume(key)['question']['answer']=='先进先出'
    reopened.reveal(key,attempt,'事后改答案')
    assert reopened.get(key)['run']['current']['answer']=='先进先出'
    result=reopened.submit(key,attempt,ticked=[])
    assert result['run']['current']['verdict']==dict(correct=False,missed=[0])
    view=reopened.next(key,attempt); attempt=view['run']['current']['attempt_id']
    reopened.reveal(key,attempt,'先进先出，队尾加入，队首移除')
    result=reopened.submit(key,attempt,ticked=[0])
    assert result['run']['current']['verdict']['correct']


def test_self_test81_multi_sets_rename_delete_remove_add_answer_isolation(tmp_path):
    store=SelfTestStore('first',tmp_path)
    key=store.create('第一份',fixture())['id']
    second=store.create('第二份',bank())['id']
    store.edit(key,name='重命名',save=True)
    body=store.get(key)
    missing=body['missing'][0]
    body=store.add_answer(key,missing['id'],'5',Locator([]))
    assert len(body['questions'])==3 and not body['missing']
    store.edit(key,remove=body['questions'][0]['id'])
    assert len(SelfTestStore('first',tmp_path).get(key)['questions'])==2
    assert store.get(key)['name']=='重命名' and not store.get(key)['draft']
    assert len(store.list())==2
    assert not SelfTestStore('second',tmp_path).list()
    store.delete(second)
    assert len(store.list())==1
    with pytest.raises(ValueError): store.get(second)


def test_self_test81_cancelled_model_does_not_create_set(tmp_path):
    cancel=Event()
    class Cancel:
        def complete(self,*args,**kwargs):
            cancel.set()
            return SimpleNamespace(text=json.dumps(dict(questions=rows())))
    current=SimpleNamespace(account_id='account',data_root=tmp_path,gateway=Cancel())
    methods={}
    bridge=SimpleNamespace(register=lambda name,fn,**kwargs:methods.setdefault(name,fn))
    register(bridge,lambda:current,None)
    with pytest.raises(ModelCancelled):
        methods['selftest.import']({'text':OBJECTIVE},SimpleNamespace(cancel=cancel))
    assert not SelfTestStore('account',tmp_path).list()


def test_self_test81_bridge_scope_and_no_plan_store_dependency(tmp_path):
    current=[SimpleNamespace(account_id='a',data_root=tmp_path,gateway=Locator(rows()))]
    methods={}
    register(SimpleNamespace(register=lambda n,f,**kw:methods.setdefault(n,f)),lambda:current[0],None)
    ctx=SimpleNamespace(cancel=Event())
    result=methods['selftest.import']({'text':OBJECTIVE+'\n\n'+RECALL+'\n\n'+MISSING},ctx)
    assert result['set']['questions'] and methods['selftest.list']({},ctx)['synced'] is False
    current[0]=SimpleNamespace(account_id='b',data_root=tmp_path,gateway=Locator([]))
    assert methods['selftest.list']({},ctx)['sets']==[]
    with pytest.raises(Exception,match='题集已不存在'):
        methods['selftest.get']({'set_id':result['set']['id']},ctx)


def test_self_test81_unkeyed_options_cannot_become_a_generated_answer():
    original='1. 哪个数是偶数？\nA. 2\nB. 3'
    raw=dict(rows()[0],source=original,answer='A')
    q=compile_row(raw,original,Locator([]))
    assert q['missing'] and 'answer' not in q


def test_self_test81_remove_question_preserves_other_progress(tmp_path):
    store=SelfTestStore('account',tmp_path,rng=random.Random(7))
    key=store.create('题集',bank(3))['id']
    view=store.start(key)
    first=view['question']['id']
    store.submit(key,view['run']['current']['attempt_id'],answer=[0])
    view=store.next(key,view['run']['current']['attempt_id'])
    removed=view['question']['id']
    store.edit(key,remove=removed)
    view=store.resume(key)
    assert view['total']==2 and removed not in view['run']['stats']
    assert view['run']['stats'][first]['streak']==1
    assert view['question']['id']!=removed


def test_self_test81_completed_set_can_add_missing_answer(tmp_path):
    store=SelfTestStore('account',tmp_path)
    extracted=fixture();extracted['questions']=extracted['questions'][:1]
    key=store.create('题集',extracted)['id']
    view=store.start(key)
    for _ in range(2):
        attempt=view['run']['current']['attempt_id']
        store.submit(key,attempt,answer=[0]);view=store.next(key,attempt)
    assert view['run']['completed']
    q=store.get(key)['missing'][0]
    body=store.add_answer(key,q['id'],'5',Locator([]))
    assert not body['run'] and len(body['questions'])==2


@pytest.mark.parametrize('answer,options,expected', [('队列',['A. 队列','B. 栈'],[0]), ('2',['1. 队列','2. 栈'],[1]), ('队列、列表',['A. 队列','B. 栈','C. 列表'],[0,2])])
def test_self_test81_original_numeric_or_literal_option_key(answer,options,expected):
    span='1. 选择题？\n'+'\n'.join(options)+'\n答案：'+answer
    q=compile_row(dict(kind='objective',objective_kind='multi' if len(expected)>1 else 'choice',stem='1. 选择题？',options=options,answer=answer,source=span),span,Locator([]))
    assert q['grading']['answer']==expected and q['options']==options and q['answer']==answer


def test_self_test81_known_question_omitted_by_model_is_visible():
    result=extract_questions(extract_bytes(RECALL.encode(),'题.md'),Locator([]))
    assert not result['questions'] and len(result['rejected'])==1
    assert result['rejected'][0]['stem']=='什么是队列？'


def test_self_test81_last_unpassed_question_keeps_two_other_questions_between(tmp_path):
    store=SelfTestStore('account',tmp_path,rng=random.Random(7))
    key=store.create('间隔练习',bank(3))['id']
    view=store.start(key,2);failed_id=view['question']['id'];failures=0;history=[]
    for _ in range(30):
        if view['run']['completed']: break
        qid=view['question']['id'];attempt=view['run']['current']['attempt_id']
        assert qid not in history[-2:]
        history.append(qid)
        wrong=qid==failed_id and failures<2
        if wrong: failures+=1
        store.submit(key,attempt,answer=[1] if wrong else [0])
        view=store.next(key,attempt)
    assert view['run']['completed'] and failures==2
    assert view['run']['stats'][failed_id]['wrong']==2
    assert any(stat['correct']>2 for qid,stat in view['run']['stats'].items() if qid!=failed_id)
