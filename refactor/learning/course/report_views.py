"""Presentation series derived only from the saved report's event-based figures."""
from __future__ import annotations


def report_views(report):
    modules = []
    for module in report['modules']:
        points = [p for p in report.get('questions',[]) if p['module_id'] == module['module_id']]
        mastered = sum(p['passed'] for p in points)
        modules.append(dict(module_id=module['module_id'], title=module['title'],
                            mastered=mastered, total=len(points)))
    history = []
    through = report['plan'].get('actual_completion_date') or report['time']['through_date']
    for row in report['time']['days']:
        if row['day'] > through:
            continue  # early completion leaves future targets for audit, not historical debt
        target = row['target_minutes']
        actual = row['credited_confirmed_minutes'] + row['credited_tentative_minutes']
        history.append(dict(day=row['day'], target_minutes=target, actual_minutes=actual,
                            confirmed_minutes=row['credited_confirmed_minutes'],
                            tentative_minutes=row['credited_tentative_minutes'],
                            debt_minutes=max(0, target - actual), ahead_minutes=max(0, actual - target),
                            segment=row['segment'], frozen=row['frozen']))
    casual = sum(p['casual_errors'] for p in report['points'])
    module = sum(p['module_errors'] for p in report.get('questions',[]))
    checks = sum(p['check_questions'] - p['check_correct'] for p in report.get('questions',[]))
    return dict(modules=modules, history=history,
                errors=dict(casual=casual, module=module, check=checks))
