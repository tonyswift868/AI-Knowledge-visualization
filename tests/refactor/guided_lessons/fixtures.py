"""Small deterministic drafts for offline classroom and bounded-review tests."""
import json


def wire(chunk='k1', *, live=False, short=False, code=None, guide=True):
    steps = [dict(text='我们先看队列的顺序。', chunk_ids=[chunk], question=None, options=[]),
             dict(text='先进入的元素先取出。[引申1]生活中排队买票可作类比，插队不在类比范围内。' +
                  ('\n```python\n' + code + '\n```' if code else ''), chunk_ids=[chunk],
                  question='甲先入队，乙后入队，通常谁先取出？' if guide else None,
                  options=[dict(text='甲', response='甲先进入，因此先取出。'),
                           dict(text='乙', response='乙后进入；队列通常先取出甲。')] if guide else [])]
    if not live: steps.append(dict(text='记住这里的先后取决于进入顺序。', chunk_ids=[chunk], question=None, options=[]))
    questions=[]
    for stem, answer in [('队列的取出顺序是什么？', '先进入的先取出'), ('甲先进入、乙后进入，谁先取出？', '甲')]:
        questions.append(dict(type='short' if short else 'choice', stem=stem,
            options=[] if short else [answer, '后进入的先取出' if answer != '甲' else '乙', '随机', '同时'],
            answer=None if short else answer, required_points=['先进入的先取出' if answer != '甲' else '甲先取出']))
    return dict(steps=steps, questions=questions, extrapolations=['排队只是生活类比，不能有人插队。'])


def draft(chunk='c1', **kwargs):
    from refactor.learning.course.lesson_prep import parse_prep
    return parse_prep(json.dumps(wire(chunk, **kwargs), ensure_ascii=False), live=kwargs.get('live', False))


def advance_to_question(room, plan_id, view, ctx):
    """Older bank/ledger integration tests skip guide UI; contract tests do not."""
    for _ in range(8):
        if view['step'] != 'lesson' or view['lesson']['awaiting'] == 'return':
            break
        if view['lesson']['awaiting'] == 'choice':
            view=room.run(plan_id,lambda co,st,cx:room.choose(co,st,cx,0),ctx)
        else:
            view=room.run(plan_id,room.next,ctx)
    return view
