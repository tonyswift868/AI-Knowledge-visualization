"""Explicit offline wiring substitute, NOT a semantic classifier or evaluator."""
import json


def wire(messages, *, duplicates=()):
    task = json.loads(messages[-1]['content'])
    return json.dumps({'comparisons': [dict(point=n['id'], previous=p['id'],
        relation='duplicate' if (n['id'],p['id']) in duplicates else 'different',
        reason='离线替身：测试预先指定的人工标签；不代表真实模型识别效果。',
        point_evidence=[n['evidence'][0]], previous_evidence=[p['evidence'][0]])
        for n in task['current'] for p in task['previous']]}, ensure_ascii=False)
