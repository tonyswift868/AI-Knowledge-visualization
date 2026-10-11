import copy
from .support97 import PLAN, make, position, act, fresh, close


def test_direct103_answers_survive_reopen_but_never_enter_teacher_or_records(tmp_path):
    e=make(tmp_path,questions=0)
    try:
        position(e,1)
        before={k:copy.deepcopy(e.store.records(k,PLAN)) for k in ('messages','judgements','question_answers','teacher_profile')}
        act(e,'start_test','skip_test')
        for i in range(3):
            answer=f'我的第{i+1}句独立回答'
            view=act(e,'reply',answer,request_id=f'answer-{i}')
            # A retried bridge request must not duplicate/replace the answer.
            act(e,'reply','不该覆盖',request_id=f'answer-{i}')
            e.room=fresh(e);view=act(e,'enter')
            assert [x['answer'] for x in view['direct_review']['entries']]==[f'我的第{j+1}句独立回答' for j in range(i+1)]
            if i<2:act(e,'direct_next')
        assert all(e.store.records(k,PLAN)==v for k,v in before.items())
        assert all('独立回答' not in str(r) for r in e.teacher.requests)
        assert act(e,'direct_choice',False)['direct_review'] is None
        assert '独立回答' not in e.room._file(PLAN).path.read_text(encoding='utf8')
    finally:close(e)


def test_direct103_old_state_without_responses_opens_and_accepts_new_answer(tmp_path):
    e=make(tmp_path,questions=0)
    try:
        act(e,'start_test','skip_test')
        state=e.room._file(PLAN).read({});state['direct_review'].pop('responses')
        e.room._file(PLAN).write(state);e.room=fresh(e)
        view=act(e,'enter')
        assert view['direct_review']['answer']==''
        assert view['direct_review']['entries'][0]['answer']==''
        assert act(e,'reply','旧状态继续回答')['direct_review']['answer']=='旧状态继续回答'
    finally:close(e)
