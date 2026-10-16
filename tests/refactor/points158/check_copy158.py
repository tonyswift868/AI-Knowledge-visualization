"""Repeatable, offline dry-run report for a copied account + apply-ready JSON.

Run with -B and a basetemp under D:\\MentorFlow\\.local\\test-tmp\\points158.
This script never writes to the source account. It opens only --data-root, which
must be the task's disposable copy, and saves its JSON report where requested.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from refactor.learning.course.store import CourseStore
from refactor.learning.course.restructure import apply_structure


def _question_stats(store, plan_id):
    rows = store.records('question_bank', plan_id)
    by_role = {}
    for row in rows:
        role = row.get('role', 'bank')
        by_role[role] = by_role.get(role, 0) + 1
    return {'total_including_retired': len(rows), 'by_role': by_role}


def inspect_copy(data_root, account_id, plan_id, structure_path, report_path):
    data_root = Path(data_root).resolve()
    task_root = Path(r'D:\MentorFlow\.local\test-tmp\points158').resolve()
    try:
        data_root.relative_to(task_root)
    except ValueError:
        raise ValueError('为避免触碰用户原库，--data-root 必须位于 D:\\MentorFlow\\.local\\test-tmp\\points158 下') from None
    structure = json.loads(Path(structure_path).read_text(encoding='utf-8'))
    if structure.get('plan_id') != plan_id:
        raise ValueError('apply-ready plan_id does not match --plan-id')
    if structure.get('regroup_subpoints') is not True:
        raise ValueError('apply-ready must explicitly set regroup_subpoints=true')
    with CourseStore(account_id, data_root) as store:
        plan = store._get('plans', plan_id, plan_id)
        before_questions = _question_stats(store, plan_id)
        before_point_count = len(store.records('points', plan_id))
        before = apply_structure(store, plan_id, structure, dry_run=True)
    report = {
        'label': 'REAL_STORE_DRY_RUN_ONLY; no apply performed',
        'data_root': str(data_root),
        'account_id': account_id,
        'plan_id': plan_id,
        'store_accepts': bool(before.get('can_apply')),
        'hard_issues': before.get('issues', []),
        'before': {
            'total_minutes': before.get('total_before', plan.get('total_minutes')),
            'point_count': before_point_count,
            'questions': before_questions,
            'overdue_to_repay': before.get('before', {}).get('overdue_to_repay'),
            'today': before.get('today'),
        },
        'after_preview': {
            'total_minutes': before.get('total_after'),
            'modules': before.get('modules', []),
            'daily_targets': before.get('daily', []),
            'overdue_to_repay': before.get('after', {}).get('overdue_to_repay'),
            'new_points': before.get('new_points'),
            'new_subpoints': before.get('new_subpoints'),
            'retained_old_points': before.get('retained_old_points'),
            'protected_old_points': before.get('protected_old_points'),
            'protected': before.get('protected'),
            'protected_subpoints': before.get('protected_subpoints'),
            'empty_regroup_groups': before.get('empty_regroup_groups'),
            'regrouped_questions': before.get('regrouped_questions'),
            # Regroup mode never retires questions; all roles migrate by sid.
            'questions_after_expected': before_questions,
            'restructure_daily_cap': before.get('daily_cap'),
        },
        'preview_token': before.get('preview_token'),
    }
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', required=True, type=Path,
                        help='Disposable copy only; never point at a live account root.')
    parser.add_argument('--account-id', required=True)
    parser.add_argument('--plan-id', required=True)
    parser.add_argument('--structure', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args(argv)
    report = inspect_copy(args.data_root, args.account_id, args.plan_id, args.structure, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['store_accepts'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
