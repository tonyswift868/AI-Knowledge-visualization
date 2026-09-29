"""Capture the extended fixed classroom with preparation and free practice."""
import argparse
import json
from pathlib import Path
import tempfile

from .audit import analyze
from .scenario import run_scenario


CLASSROOM_PURPOSES = frozenset({
    'teacher', 'judge', 'questions', 'profile', 'lesson_prep',
    'lesson_review', 'check_profile',
})


def classroom_only(capture):
    return dict(capture, requests=[row for row in capture['requests']
        if row['purpose'] in CLASSROOM_PURPOSES and (
            not capture['practice_reply_lost_pending'] or row['index'] < capture['practice_start'])])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='classroom-', dir=args.out) as root:
        capture = classroom_only(run_scenario(Path(root), extended=True))
    analysis = analyze(capture, block=64)
    for name, value in [('requests.json', capture), ('analysis.json', analysis)]:
        (args.out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(analysis['total'], ensure_ascii=False))


if __name__ == '__main__':
    main()
