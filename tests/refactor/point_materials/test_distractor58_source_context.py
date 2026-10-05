"""Replay the four saved small57 failures, without any model/network calls."""
import copy
import json
from pathlib import Path
import pytest
from refactor.learning.course import point_materials as pm, question_bank as qb

FIXTURE=Path(__file__).parent/'fixtures/small57_distractors.json'


def records58():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def pending58(data):
    return [{k:copy.deepcopy(v) for k,v in q.items() if k!='distractor_check'} for q in data['questions']]


def visible_reply58(packet, **kwargs):
    """Explicit fixture response when the checker sees the missing paragraph.

    This is not an automated semantic grader; the support decisions are the
    human small57 labels, with literal quotes from its saved original material.
    """
    texts={p['id']:p['text'] for p in packet['passages']}
    results=[]
    for q in packet['questions']:
        checks=[]
        for option in q['distractor_passages']:
            i=option['index'];value=q['options'][i]
            quote=''
            if value in ('withdraw cash','use a calculator'):
                wanted="When you withdraw cash from an ATM, scan groceries at the store, or use a calculator, you're using a type of computer."
                quote=next((wanted for cid in option['passage_ids'] if wanted in texts[cid]),'')
            elif value in ('at work, home, and school','on a desk'):
                wanted='Laptops are battery-powered computers that are more portable than desktops, allowing you to use them almost anywhere.'
                quote=next((wanted for cid in q['passage_ids'] if wanted in texts[cid]),'') if option['passage_ids'] else ''
            elif value=='the computer case, monitor, keyboard, and mouse':
                wanted="Desktop computers are designed to be placed on a desk, and they're typically made up of a few different parts, including the computer case, monitor, keyboard, and mouse."
                quote=next((wanted for cid in option['passage_ids'] if wanted in texts[cid]),'')
            checks.append(dict(index=i,verdict='also_supported' if quote else 'not_supported',quote=quote))
        results.append(dict(id=q['id'],checks=checks))
    return dict(questions=results)


@pytest.mark.parametrize('with_contrasts',[True,False])
def test_distractor58_saved_four_failures_include_origin_and_discard_unsafe_questions(with_contrasts):
    data=records58();questions=pending58(data);calls=[]
    if not with_contrasts:
        for q in questions:q.pop('contrast_evidence')
    def checker(packet,**kw):
        calls.append(packet)
        for sent,q in zip(packet['questions'],data['questions']):
            origins={c['chunk_id'] for c in q['contrast_evidence']}
            assert origins<=set(sent['passage_ids'])
            assert {i['index'] for i in sent['distractor_passages']}==set(range(len(q['options'])))-set(q['answer'])
            assert sent['answer']==q['answer'] and sent['options']==q['options'] and sent['stem']==q['stem']
        return visible_reply58(packet)
    rows=pm.check_distractors(questions,data['points'],data['passages'],dict(distractor_checker=checker))
    assert len(calls)==1
    assert all(q['distractor_check']['state']=='rejected' for q in rows)
    assert [sum(c['removed'] for c in q['distractor_check']['checks']) for q in rows]==[2,1,2,2]
    assert all(q['distractor_check']['reason']=='删除后不足两个干扰项' for q in rows)
    assert all(not qb.checked_question(q) for q in rows)
    assert all(qb.select_question(rows,p['id'],'module') is None for p in data['points'])
    assert pm.check_distractors(rows,data['points'],data['passages'],dict(distractor_checker=lambda *a:pytest.fail('rechecked')))==rows


def test_distractor58_saved_prechange_checks_kept_all_unsafe_options():
    data=records58()
    assert len(data['questions'])==4
    assert all(q['distractor_check']['state']=='passed' for q in data['questions'])
    assert all(not c['removed'] and c['verdict']=='not_supported' for q in data['questions'] for c in q['distractor_check']['checks'])


def test_distractor58_origin_is_per_option_and_does_not_expand_to_other_materials():
    data=records58();q=pending58(data)[0];passages=copy.deepcopy(data['passages'])
    foreign=copy.deepcopy(passages[0]);foreign.update(id='foreign',source_id='other-book',text='withdraw cash use a calculator')
    foreign['source']['locator']='foreign paragraph';passages.append(foreign)
    q['contrast_evidence'][0]['chunk_id']='foreign'
    seen=[]
    def checker(packet,**kw):
        seen.append(packet)
        assert 'foreign' not in packet['questions'][0]['passage_ids']
        assert 'foreign' not in {p['id'] for p in packet['passages']}
        return visible_reply58(packet)
    row=pm.check_distractors([q],data['points'],passages,dict(distractor_checker=checker))[0]
    assert row['distractor_check']['state']=='rejected' and len(seen)==1


def test_distractor58_origin_quote_can_remove_one_option_and_keep_existing_remap():
    data=records58();q=pending58(data)[0]
    q.update(kind='choice',answer=[0],options=['browse the Web','use a calculator','invalid-X','invalid-Y'])
    original=copy.deepcopy(q)
    row=pm.check_distractors([q],data['points'],data['passages'],dict(distractor_checker=visible_reply58))[0]
    assert qb.checked_question(row),row['distractor_check']
    assert row['options']==['browse the Web','invalid-X','invalid-Y'] and row['answer']==[0]
    assert row['stem']==original['stem'] and row['contrast_evidence']==original['contrast_evidence']
