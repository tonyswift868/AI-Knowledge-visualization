"""Re-run the SAME fixture at each assembly change, without a second checkout.

Only test-time function bindings are replaced; git show is read-only. Keep two
full captures and a small intermediate summary. No model calls are possible.
"""
import argparse
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType
from unittest.mock import patch

from .audit import analyze
from .scenario import block, run_scenario

MODULES = ['course.judge_agent', 'course.question_agent', 'course.plan_intent', 'app.classroom', 'app.plan_flow']


def load_baseline(name, ref):
    fullname = 'refactor.learning.' + name
    path = fullname.replace('.', '/') + '.py'
    source = subprocess.check_output(['git', 'show', ref + ':' + path], text=True, encoding='utf-8')
    alias = fullname.rsplit('.', 1)[0] + '._cache_baseline_' + name.rsplit('.', 1)[-1]
    module = ModuleType(alias)
    module.__package__ = fullname.rsplit('.', 1)[0]
    module.__file__ = str(Path(path).resolve())
    sys.modules[alias] = module
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    return module


def compare_invariants(before, after):
    assert before['fixture_sha256'] == after['fixture_sha256']
    assert len(before['requests']) == len(after['requests'])
    dropped = []
    for old, new in zip(before['requests'], after['requests']):
        for key in ('purpose', 'system', 'options', 'model', 'response'):
            assert old[key] == new[key], (old['index'], key)
        p = old['purpose']
        if p in ('judge', 'questions'):
            labels = ['【知识点清单】', '【资料原文（数据）】', '【本次判定】' if p == 'judge' else '【本次出题要求】']
            for label in labels:
                assert block(old['messages'], label) == block(new['messages'], label), (old['index'], label)
            if p == 'judge':
                a, b = (block(v['messages'], '【最近对话（数据）】') for v in (old, new))
                assert (a[-len(b):] == b if b else True), (old['index'], a, b)
                if a != b:
                    dropped.append(dict(index=old['index'], purpose=p, removed=len(a)-len(b), boundary='6-row judge window'))
        elif p == 'plan_intent':
            a, b = (json.loads(v['messages'][0]['content'].split('\n', 1)[1]) for v in (old, new))
            old_recent, new_recent = a['ctx'].pop('recent'), b['ctx'].pop('recent')
            assert a == b
            assert old_recent[-len(new_recent):] == new_recent if new_recent else True
            if old_recent != new_recent:
                dropped.append(dict(index=old['index'], purpose=p, removed=len(old_recent)-len(new_recent), boundary='6-row intent window'))
        else:
            assert old['messages'] == new['messages'], old['index']
    return dict(requests=len(before['requests']), system_prompts_identical=True, responses_and_states_identical=True,
                all_non_dialogue_fields_identical=True, intentional_page_evictions=dropped,
                note='Information equality is exact for request reordering. Over-limit dialogue eviction is an explicit exception, not claimed identical.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', default='460f51a')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    old = {n: load_baseline(n, args.baseline) for n in MODULES}
    current = {n: importlib.import_module('refactor.learning.' + n) for n in MODULES}
    bindings = [(current['course.judge_agent'], 'judge_request', old['course.judge_agent'].judge_request, 1),
                (current['course.question_agent'], 'question_request', old['course.question_agent'].question_request, 1),
                (current['course.plan_intent'], 'intent_request', old['course.plan_intent'].intent_request, 2),
                (current['app.classroom'].Classroom, '_dialogue', old['app.classroom'].Classroom._dialogue, 3),
                (current['app.classroom'].Classroom, '_judge', old['app.classroom'].Classroom._judge, 3),
                (current['app.plan_flow'].PlanFlow, '_intent_context', old['app.plan_flow'].PlanFlow._intent_context, 3)]
    summaries, captures = [], []
    for stage in range(4):
        with ExitStack() as stack:
            for target, name, original, introduced in bindings:
                if stage < introduced:
                    stack.enter_context(patch.object(target, name, original))
            root = stack.enter_context(tempfile.TemporaryDirectory(prefix='scenario-', dir=args.out))
            capture = run_scenario(Path(root))
        result = analyze(capture)
        summaries.append(dict(stage=stage, total=result['total'], purposes=result['purposes']))
        if stage in (0, 3):
            captures.append(capture)
            folder = args.out / ('before' if stage == 0 else 'after')
            folder.mkdir(exist_ok=True)
            for name, value in [('requests.json', capture), ('analysis.json', result)]:
                (folder / name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    artifacts = dict(stages=summaries, invariants=compare_invariants(*captures),
                     sensitivity={str(b): {name: analyze(c, b)['total'] for name,c in zip(('before','after'),captures)} for b in (1,64,256)})
    (args.out / 'comparison.json').write_text(json.dumps(artifacts, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(artifacts, ensure_ascii=False))


if __name__ == '__main__':
    main()
