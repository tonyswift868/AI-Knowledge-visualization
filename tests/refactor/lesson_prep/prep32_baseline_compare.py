"""Offline paired sample against the assigned baseline; no paid gateway."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from tests.refactor.guided_lessons.test_guided_classroom_contract import env, PLAN


def compare(root):
    source = subprocess.check_output(['git', 'show', 'dd0215f:refactor/learning/app/classroom.py'], cwd=REPO).decode('utf-8')
    before_file = root/'baseline-classroom.py'
    before_file.write_text(source, encoding='utf-8')
    spec = importlib.util.spec_from_file_location('refactor.learning.app._baseline32', before_file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    results = []
    for version in ('before', 'after'):
        folder = root/f'comparison-{version}-{time.time_ns()}'; folder.mkdir(exist_ok=True)
        patches = pytest.MonkeyPatch()
        fixture = env.__wrapped__(folder, patches)
        e = next(fixture)
        if version == 'before':
            e.room = module.Classroom(e.services, lambda: __import__('datetime').date(2026, 10, 1))
        e.room._prepare_later = lambda *a, **k: None
        e.room._schedule_idle_prep = lambda *a, **k: None
        e.room._prefetch_banks = lambda *a, **k: None
        complete = e.gateway.complete
        def slow(purpose, messages, **kwargs):
            if purpose in ('lesson_prep', 'lesson_review'): time.sleep(.55)
            return complete(purpose, messages, **kwargs)
        e.gateway.complete = slow
        timings = []
        def act(method, *args):
            start = time.perf_counter()
            view = e.room.run(PLAN, lambda co, st, cx: getattr(e.room, method)(co, st, cx, *args))
            timings.append(dict(method=method, seconds=time.perf_counter()-start, step=view['step']))
            # Learner waits for delivery outside the measured bridge call.
            while view['step'] == 'preparing_lesson':
                time.sleep(.01)
                view = e.room.run(PLAN, lambda *a: None)
            return view
        try:
            act('enter'); act('next'); act('next'); act('choose', 0); act('next'); act('next')
            view = act('reply', '先进入的先取出')
            assert view['lesson']['point_id'] == 'p01'
            results.append(dict(version=version, model_sleep_seconds=.55, timings=timings,
                                calls=e.gateway.calls, judgements=len(e.store.records('judgements', PLAN))))
        finally:
            while getattr(e.room, '_lesson_wait_jobs', set()): time.sleep(.01)
            try: next(fixture)
            except StopIteration: pass
            patches.undo()
    (root/'paired-comparison.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    compare(Path(os.environ['MF32_ARTIFACTS']))
