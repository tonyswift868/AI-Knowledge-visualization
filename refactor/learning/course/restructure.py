"""Offline, transactional replacement of explicitly covered, unstudied points.

apply-ready: {plan_id, today?, modules: [{module_id, replace_point_ids,
points: [concept_v1 payloads], navigation: [...]}]}. Explicit IDs prevent a
stale proposal or a module number from silently broadening the replacement.
No model, files, network, schema migration or shared service is owned here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date
import hashlib
import json

from .ledger import Ledger, LedgerError
from .store import StoreError, _window, _window_json


class RestructureError(StoreError):
    def __init__(self, report):
        self.report = report
        super().__init__('RESTRUCTURE_REFUSED', '结构有不一致，未执行：' + '；'.join(report['issues']))


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def _point_mentions(value, point_ids):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('point_id', 'unit_id') and isinstance(item, str) and item in point_ids:
                found.add(item)
            elif key in ('point_ids', 'wrong_points', 'taught', 'skipped_points') and isinstance(item, list):
                found.update(p for p in item if isinstance(p, str) and p in point_ids)
            elif key == 'results' and isinstance(item, list):
                found.update(p[0] for p in item if isinstance(p, (list, tuple)) and p and p[0] in point_ids)
            if isinstance(item, (dict, list)): found.update(_point_mentions(item, point_ids))
    elif isinstance(value, list):
        for item in value: found.update(_point_mentions(item, point_ids))
    return found


def preserved_points(store, plan_id):
    """Original facts, including revoked credits, are evidence of prior activity."""
    modules = store.records('modules', plan_id)
    points = store.records('points', plan_id)
    pids = {p['id'] for p in points}
    reasons = {pid: set() for pid in pids}
    groups = {m['id']: sorted((p for p in points if p['module_id'] == m['id']),
                             key=lambda p: (p['position'], p['id'])) for m in modules}
    rank = {m['id']: m['position'] for m in modules}
    position = store.classroom_position(plan_id)
    scene = position['state'] if position else {}
    current = groups.get(scene.get('module_id'), [])
    index = scene.get('index', 0)
    cursor = None
    if type(index) is int and 0 <= index <= len(current) and scene.get('module_id') in rank:
        cursor = current[index]['id'] if index < len(current) else None
        for p in points:
            if (rank[p['module_id']] < rank[scene['module_id']] or
                    p['module_id'] == scene['module_id'] and p in current[:index]):
                reasons[p['id']].add('课堂当前位置之前')
        if cursor and scene.get('step') not in ('opening', 'teach'):
            reasons[cursor].add('课堂已进入此点的作答/检测环节')
        if cursor and scene.get('step') == 'teach' and scene.get('conversation'):
            reasons[cursor].add('课堂正在讲此点')
    for p in points:
        if p.get('taught') or p.get('skipped') or p.get('state') in ('passed', 'skipped', 'taught', 'completed'):
            reasons[p['id']].add('知识点已有学习/跳过标记')
    for entity in ('credits', 'judgements', 'question_answers', 'practice_results', 'usage'):
        for row in store.records(entity, plan_id):
            for pid in _point_mentions(row, pids): reasons[pid].add(entity)
            if entity == 'credits':
                unit = row['credit']['unit_id']
                if unit.startswith('test:') and unit[5:] in groups:
                    for p in groups[unit[5:]]: reasons[p['id']].add('模块检测时长记录')
                elif unit.startswith('check:'):
                    question = next((q for q in store.records('question_bank', plan_id) if q['id']==unit[6:]), None)
                    for pid in question['point_ids'] if question else []:
                        if pid in reasons: reasons[pid].add('最终检验时长记录')
    for row in store.records('attempt_closed', plan_id):
        for p in groups.get(row['event']['module_id'], []): reasons[p['id']].add('模块检测/跳过记录')
    for row in store.records('ledger_events', plan_id):
        for pid in _point_mentions(row, pids): reasons[pid].add('账本进度记录')
    if scene.get('pending') or scene.get('test') or scene.get('check'):
        for pid in _point_mentions({k: scene.get(k) for k in ('pending','test','check')}, pids):
            reasons[pid].add('课堂未结束的作答/检测')
    for row in store.records('messages', plan_id):
        explicit = _point_mentions(row, pids)
        for pid in explicit: reasons[pid].add('课堂消息')
        # Legacy messages have only module_id. The cursor protects their prefix;
        # a previously visited later module must be retained conservatively.
        if (not explicit and row.get('role') == 'assistant' and row.get('kind') in ('teach', 'opening', 'question')
                and (not scene or rank.get(row.get('module_id'), -1) > rank.get(scene.get('module_id'), -1))):
            for p in groups.get(row.get('module_id'), []): reasons[p['id']].add('旧消息无法定位到点，保留该模块')
    # Retiring a shared question must not change assessment of a retained point.
    for question in store.records('question_bank', plan_id):
        if any(reasons.get(pid) for pid in question['point_ids']):
            for pid in question['point_ids']:
                if pid in reasons: reasons[pid].add('题目与有进度的点共享，保留关联点')
    return {pid: sorted(why) for pid, why in reasons.items() if why}, cursor


def _calendar(ledger, today):
    status = asdict(ledger.status(today))
    return dict(today_target=status['today_target'], overdue_to_repay=status['debt_minutes'] + status['history_debt_minutes'],
                debt_minutes=status['debt_minutes'], history_debt_minutes=status['history_debt_minutes'],
                today_confirmed=status['today_confirmed'], today_tentative=status['today_tentative'],
                today_repaid_confirmed=status['today_repaid_confirmed'],
                today_repaid_tentative=status['today_repaid_tentative'],
                targets={day.isoformat(): minutes for day, minutes in sorted(ledger.targets.items())},
                remaining_minutes=status['remaining_minutes'])


def _prepare(store, plan_id, structure, *, end_date=None):
    if not isinstance(structure, dict): raise StoreError('INVALID_STRUCTURE', '结构文件必须是 JSON 对象。')
    structure = deepcopy(structure)
    plan = store._get('plans', plan_id, plan_id)
    today = date.fromisoformat(structure.get('today') or date.today().isoformat())
    issues = []
    original_window = _window(plan['window'])
    requested_end = original_window.end
    valid_end = True
    if end_date is not None:
        try:
            # fromisoformat also accepts compact dates; CLI/API require YYYY-MM-DD.
            requested_end = date.fromisoformat(end_date)
            if requested_end.isoformat() != end_date:
                raise ValueError()
            if requested_end < today or requested_end < original_window.start:
                raise ValueError()
        except (TypeError, ValueError):
            issues.append('新截止日必须为 YYYY-MM-DD，且不能早于今天或原开始日')
            requested_end, valid_end = original_window.end, False
    new_window = replace(original_window, end=requested_end)
    deadline_changed = valid_end and requested_end != original_window.end
    if deadline_changed:
        issues.append('截止日变更目前只能预演，不能执行：现有 Ledger.replan(preserve_past=True) 禁止改变窗口，'
                      'CourseStore.replan_plan 固定写 explicit_replan，无法以 restructure 原样回放；'
                      '需同时调整 store.py 与 ledger.py（不在本任务允许修改范围内）')
    if structure.get('plan_id') != plan_id: issues.append('结构文件的计划 id 不一致')
    if structure.get('complete') is False: issues.append('结构文件标记为未完成，不能执行')
    if structure.get('quality_review', {}).get('passed') is False:
        issues.append('结构质量检查未通过：' + str(structure['quality_review'].get('reason', '请先完成结构')))
    scopes = structure.get('modules')
    if not isinstance(scopes, list) or not scopes:
        raise StoreError('INVALID_STRUCTURE', '结构必须明确列出 modules、replace_point_ids、points、navigation。')
    modules = {m['id']: m for m in store.records('modules', plan_id)}
    points = {p['id']: p for p in store.records('points', plan_id)}
    protected, cursor = preserved_points(store, plan_id)
    chunk_ids = {c['id'] for c in store.records('source_chunks', plan_id)}
    scene = store.classroom_position(plan_id)
    if scene:
        state = scene['state']
        count = sum(p['module_id'] == state.get('module_id') for p in points.values())
        if state.get('module_id') not in modules or type(state.get('index')) is not int or not 0 <= state['index'] <= count:
            issues.append('课堂位置不能对应现有模块/知识点，拒绝猜测当前进度')
        if state.get('point_id') and state['point_id'] != cursor:
            issues.append('课堂 point_id 与模块/index 不一致，拒绝猜测当前进度')
    changed, replacement_ids, new_ids, seen_modules = [], set(), set(), set()
    for scope in scopes:
        if (not isinstance(scope, dict) or not isinstance(scope.get('module_id'), str)
                or scope['module_id'] not in modules):
            issues.append('结构引用的模块不存在'); continue
        mid = scope['module_id']
        if mid in seen_modules: issues.append('同一模块重复出现：' + mid); continue
        seen_modules.add(mid)
        old_module = modules[mid]
        members = sorted((p for p in points.values() if p['module_id'] == mid), key=lambda p: (p['position'], p['id']))
        ids = scope.get('replace_point_ids')
        if not isinstance(ids, list) or not ids or any(not isinstance(pid, str) for pid in ids) or len(ids) != len(set(ids)):
            issues.append(mid + '：必须明确列出不重复的 replace_point_ids'); continue
        missing = [pid for pid in ids if pid not in points or points[pid]['module_id'] != mid]
        if missing: issues.append(mid + '：要取代的旧点不存在/不属于此模块：' + ', '.join(missing)); continue
        replacement_ids.update(ids)
        for pid in ids:
            if pid in protected: issues.append('要取代的点已有进度，拒绝：' + pid + '（' + '、'.join(protected[pid]) + '）')
        covered = sorted((points[pid] for pid in ids), key=lambda p: (p['position'], p['id']))
        indexes = [members.index(p) for p in covered]
        if indexes != list(range(min(indexes), max(indexes) + 1)):
            issues.append(mid + '：取代范围必须连续，不能穿过保留点')
        if old_module['point_count'] != len(members) or old_module['learning_minutes'] != sum(p['minutes'] for p in members):
            issues.append(mid + '：模块点数/学习分钟与实际知识点不一致，请先修复或完成同步')
        expected_points = scope.get('expected_points', [])
        if not isinstance(expected_points, list): issues.append(mid+'：expected_points 必须是列表'); expected_points=[]
        for expected in expected_points:
            if not isinstance(expected, dict): issues.append(mid+'：旧点快照格式无效'); continue
            if expected.get('id') not in points or expected != points[expected['id']]:
                issues.append(mid + '：旧点内容已变化，请重新生成结构/预演：' + str(expected.get('id')))
        rows = scope.get('points')
        navigation = scope.get('navigation')
        if not isinstance(rows, list) or not isinstance(navigation, list):
            issues.append(mid + '：points 与 navigation 必须是列表'); continue
        first = covered[0]['position']
        prepared = []
        kept = [p for p in members if p['id'] not in ids]
        positions = {p['position'] for p in kept}
        for i, incoming in enumerate(rows):
            if not isinstance(incoming, dict): issues.append(mid + '：新点必须是对象'); continue
            point = dict(incoming, module_id=mid, position=first+i)
            pid = point.get('id')
            if not isinstance(pid, str): issues.append(mid + '：新点 id 无效'); continue
            if pid in points or pid in new_ids: issues.append('新点 id 已存在/重复：' + pid)
            new_ids.add(pid)
            if point['position'] in positions: issues.append('新点位置会覆盖保留点：' + pid)
            if point.get('structure') != 'concept_v1' or 'goal' not in point or not point.get('subpoints'):
                issues.append('新点缺少 concept_v1 完整字段：' + pid)
            for sub in point.get('subpoints', []) if isinstance(point.get('subpoints'), list) else []:
                if not isinstance(sub, dict) or not sub.get('notes'):
                    issues.append('新小要点没有讲义，不能执行：' + str(sub.get('id') if isinstance(sub, dict) else pid))
            for cid in point.get('chunk_ids', []) if isinstance(point.get('chunk_ids'), list) else []:
                if isinstance(cid, str) and cid not in chunk_ids: issues.append('新点 '+pid+' 引用不存在的本计划 chunk：'+cid)
            try: store._validate('points', plan_id, point)
            except (StoreError, KeyError, TypeError, ValueError) as exc:
                issues.append('新点校验失败：' + pid + '（' + getattr(exc, 'code', type(exc).__name__) + '）')
            prepared.append(point)
        if any(type(p.get('minutes')) is not int or p['minutes'] <= 0 for p in prepared): continue
        updated = dict(old_module, navigation=navigation, point_count=len(kept)+len(prepared),
                       learning_minutes=sum(p['minutes'] for p in kept+prepared),
                       question_extraction=dict(state='pending', reason='restructure'),
                       material_preparation=dict(old_module.get('material_preparation') or {}, state='pending',
                           error=None, message='这个模块的题需要按资料重新准备，学习进度已保留。'))
        if not updated['point_count']: issues.append(mid+'：重切后没有可学习的点，现有课堂尚不能接续空模块')
        if cursor in ids and not prepared: issues.append(mid+'：当前位置被取代，但没有可接续的新点')
        for nav in navigation:
            for cid in nav.get('chunk_ids', []) if isinstance(nav, dict) and isinstance(nav.get('chunk_ids'), list) else []:
                if isinstance(cid, str) and cid not in chunk_ids: issues.append('导航引用不存在的本计划 chunk：'+cid)
        try: store._validate('modules', plan_id, updated)
        except (StoreError, KeyError, TypeError, ValueError) as exc:
            issues.append('模块导航校验失败：' + mid + '（' + getattr(exc, 'code', type(exc).__name__) + '）')
        changed.append(dict(old=old_module, new=updated, points=prepared, replace=ids))
    before_ledger = store._load_ledger(plan)
    total = plan['total_minutes'] + sum(c['new']['learning_minutes']-c['old']['learning_minutes'] for c in changed)
    after_ledger = Ledger.from_dict(before_ledger.to_dict())
    try:
        # Calculation on a detached clone only. Never publish this window as a
        # restructure event: the current store's replay would reject it. Keeping
        # the FIFO segment and prefix allows an honest, read-only date preview.
        if deadline_changed:
            after_ledger._window = new_window
        after_ledger.replan(today, new_window, total, preserve_past=True)
    except (StoreError, LedgerError, ValueError) as exc: issues.append('无法重排账本：' + str(exc))
    before, after = _calendar(before_ledger, today), _calendar(after_ledger, today)
    if any(before['targets'].get(day) != after['targets'].get(day) for day in before['targets'] if day < today.isoformat()):
        issues.append('历史目标发生变化，拒绝执行')
    questions = [q for q in store.records('question_bank', plan_id) if set(q['point_ids']) & replacement_ids and q.get('role') != 'retired']
    journal = [tuple(row) for row in store._db.execute('SELECT entity,entity_id,payload,deleted,local_revision FROM _records WHERE plan_key=? ORDER BY entity,entity_id', (plan_id,))]
    token_input = [structure, journal, today.isoformat()]
    if end_date is not None: token_input.append(end_date)
    token = _digest(token_input)
    future = {day: minutes for day, minutes in after['targets'].items()
              if today.isoformat() <= day <= requested_end.isoformat()}
    over_cap = [dict(day=day, target=minutes, excess_minutes=minutes-600)
                for day, minutes in future.items() if minutes > 600]
    allocation = after_ledger._allocation()
    outstanding = after['overdue_to_repay'] + sum(max(0, minutes
        - allocation.confirmed.get(date.fromisoformat(day), 0)
        - allocation.tentative.get(date.fromisoformat(day), 0)) for day, minutes in future.items())
    remaining_days = max(0, (requested_end-max(today, original_window.start)).days+1)
    capacity = remaining_days * 600
    daily_excess = sum(row['excess_minutes'] for row in over_cap)
    capacity_excess = max(0, outstanding-capacity)
    report = dict(plan_id=plan_id, title=plan.get('title') or plan['goal'], today=today.isoformat(),
                  can_apply=not issues, issues=issues, preview_token=token,
                  retained_old_points=len(points)-len(replacement_ids), protected_old_points=len(protected),
                  protected=protected, replaced_old_points=len(replacement_ids), new_points=len(new_ids),
                  new_subpoints=sum(len(p.get('subpoints', [])) for c in changed for p in c['points']),
                  navigation_entries=sum(len(c['new']['navigation']) for c in changed), retired_questions=len(questions),
                  classroom_redirect=cursor in replacement_ids,
                  modules=[dict(id=c['old']['id'], title=c['old']['title'], before=c['old']['learning_minutes'],
                                after=c['new']['learning_minutes'], before_points=c['old']['point_count'], after_points=c['new']['point_count'],
                                covered_before=sum(points[pid]['minutes'] for pid in c['replace']),
                                covered_after=sum(p['minutes'] for p in c['points'])) for c in changed],
                  total_before=plan['total_minutes'], total_after=total,
                  end_date_before=original_window.end.isoformat(), end_date_after=requested_end.isoformat(),
                  remaining_days=remaining_days,
                  deadline_change_supported=not deadline_changed,
                  daily_cap=dict(minutes=600, fits=not over_cap and not capacity_excess, over_cap_days=over_cap,
                                 excess_minutes=max(daily_excess, capacity_excess),
                                 daily_target_excess_minutes=daily_excess,
                                 total_capacity_excess_minutes=capacity_excess,
                                 capacity_minutes=capacity, outstanding_minutes=outstanding,
                                 average_minutes_including_overdue=(outstanding+remaining_days-1)//remaining_days
                                     if remaining_days else None,
                                 maximum_daily_minutes=max(future.values(), default=0),
                                 includes_overdue=True),
                  retained_estimate_offset=plan['total_minutes']-store._total(plan_id), before=before, after=after,
                  daily=[dict(day=day, before=before['targets'].get(day,0), after=after['targets'].get(day,0))
                         for day in sorted(set(before['targets']) | set(after['targets']))
                         if today.isoformat() <= day <= requested_end.isoformat()])
    return report, changed, questions, cursor, plan


def apply_structure(store, plan_id, structure, *, dry_run, end_date=None):
    """Preview without writes; otherwise validate and commit all or nothing.

    end_date is optional YYYY-MM-DD. Changed deadlines are forecast only until
    the existing store/ledger support preserving past targets across windows.
    """
    with store.transaction():
        report, changed, questions, cursor, plan = _prepare(store, plan_id, structure, end_date=end_date)
        if dry_run: return report
        if report['issues']: raise RestructureError(report)
        for q in questions:
            q.update(role='retired', retirement=dict(version=8, reason='restructure', preparation_reset=True,
                                                    structure=report['preview_token']))
            store._put('question_bank', plan_id, q)
        for change in changed:
            for point in change['points']: store._put('points', plan_id, point)
            for pid in change['replace']:
                old = store._get('points', plan_id, pid)
                store._put('points', plan_id, dict(old, replaced_by=[p['id'] for p in change['points']],
                           replacement_operation=report['preview_token']))
            replaced = set(change['replace'])
            for links in store.records('point_links', plan_id):
                kept = [edge for edge in links['links'] if not {edge['from'], edge['to']} & replaced]
                if kept != links['links']:
                    store._put('point_links', plan_id, dict(links, links=kept, status='pending'))
            store._put('modules', plan_id, change['new'])
        if report['classroom_redirect']:
            change = next(c for c in changed if cursor in c['replace'])
            if not change['points']: raise StoreError('EMPTY_CURSOR_MODULE', '当前位置没有可接续的新点。')
            state = deepcopy(store.classroom_position(plan_id)['state'])
            first = change['points'][0]
            ordered = [p for p in store.records('points', plan_id) if p['module_id'] == first['module_id']]
            state.update(module_id=first['module_id'], index=next(i for i,p in enumerate(ordered) if p['id']==first['id']),
                         step='teach', phase='learning', pending=None, test=None, check=None)
            if 'point_id' in state: state['point_id'] = first['id']
            for key in ('conversation', 'recall_result', 'answer_explanation'): state.pop(key, None)
            store.save_classroom_position(plan_id, state)
        store._replan(plan, date.fromisoformat(report['today']), _window(plan['window']), report['total_after'],
                      cause='restructure', reason='按已确认的知识点结构重排；预演 ' + report['preview_token'])
        store._put('plans', plan_id, plan)
        return report


def format_report(report):
    lines = [f"计划：{report['title']}（{report['plan_id']}），按 {report['today']} 计算",
             '可执行' if report['can_apply'] else '不能执行：请先解决下面的问题',
             f"保留旧点 {report['retained_old_points']}（其中有进度/位于当前位置之前 {report['protected_old_points']}）；"
             f"取代 {report['replaced_old_points']}；新增大点 {report['new_points']}；资料导航 {report['navigation_entries']} 条（不计时、不考核）。",
             f"新大点包含 {report['new_subpoints']} 个小要点。",
             f"题库退役 {report['retired_questions']} 道；课堂位置{'将接到第一个新点' if report['classroom_redirect'] else '保持不变'}。"]
    lines += [f"{m['title']}：学习分钟 {m['before']} → {m['after']}；点数 {m['before_points']} → {m['after_points']}" for m in report['modules']]
    lines += [f"  本次取代范围：{m['covered_before']} → {m['covered_after']} 分钟" for m in report['modules']]
    lines += [f"计划总分钟：{report['total_before']} → {report['total_after']}",
              f"截止日：{report['end_date_before']} → {report['end_date_after']}；剩余 {report['remaining_days']} 天（含今天/原开始日）",
              f"逾期待还：{report['before']['overdue_to_repay']} → {report['after']['overdue_to_repay']} 分钟",
              f"今天目标：{report['before']['today_target']} → {report['after']['today_target']} 分钟",
              '今天及以后每天目标：']
    lines += [f"  {row['day']}：{row['before']} → {row['after']} 分钟" for row in report['daily']]
    cap = report['daily_cap']
    lines.append(f"按每天上限 {cap['minutes']} 分钟：{'排得下' if cap['fits'] else '排不下'}；"
                 f"最高每天 {cap['maximum_daily_minutes']} 分钟；累计超出 {cap['excess_minutes']} 分钟。"
                 f"含逾期未还共 {cap['outstanding_minutes']} 分钟，剩余天数容量 {cap['capacity_minutes']} 分钟，"
                 f"总容量缺口 {cap['total_capacity_excess_minutes']} 分钟；未自动改日期或目标。")
    lines += [f"  {row['day']} 超出 {row['excess_minutes']} 分钟" for row in cap['over_cap_days']]
    if cap['average_minutes_including_overdue'] is not None:
        lines.append(f"若要到截止日同时学完并还清逾期，平均每天约 {cap['average_minutes_including_overdue']} 分钟"
                     '（仅容量估算，不改每日目标或欠账）。')
    if report['retained_estimate_offset']:
        lines.append(f"保留原计划相对模块合计的 {report['retained_estimate_offset']} 分钟估时差额；仅调整本次重切的增减量，不消掉旧欠账。")
    lines += ['历史日期的目标与全部原进度不改。', '确认用的预演编号：' + report['preview_token']]
    lines += ['问题：' + issue for issue in report['issues']]
    return '\n'.join(lines)
