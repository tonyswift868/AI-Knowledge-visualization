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

from .ledger import Ledger, LedgerError, weight_of
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


def _regroup_points(store, plan_id, module_points, requested_ids, incoming_points,
                    protected_ids, chunk_ids, issues, module_id):
    """Re-home existing subpoints while preserving their source material.

    The proposal controls only big-point grouping, names and goals. Small-point
    payloads are copied from the live records; only their titles may change.
    """
    source_subpoints = {}
    source_markers = {}
    for point in sorted(module_points, key=lambda p: (p['position'], p['id'])):
        marker = point.get('subpoint_preparation')
        if isinstance(marker, dict):
            source_markers[point['id']] = marker
        for sub_index, subpoint in enumerate(point.get('subpoints', [])):
            sid = subpoint.get('id')
            if sid in source_subpoints:
                issues.append(module_id + '：旧结构的小要点 id 重复，无法安全重分组：' + str(sid))
            else:
                source_subpoints[sid] = (point['id'], subpoint, (point['position'], point['id'], sub_index))

    protected_subpoints = {sid for sid, (owner, _, _) in source_subpoints.items()
                           if owner in protected_ids}
    expected = {sid for sid, (owner, _, _) in source_subpoints.items()
                if owner in requested_ids and owner not in protected_ids}
    seen = []
    cleaned = []
    subpoint_renames = {}
    empty_ids = []
    for incoming in incoming_points:
        if not isinstance(incoming, dict):
            issues.append(module_id + '：新点必须是对象')
            continue
        raw_subpoints = incoming.get('subpoints')
        if not isinstance(raw_subpoints, list):
            issues.append(module_id + '：重分组的大点必须包含小要点列表')
            continue
        kept_subpoints = []
        for proposed in raw_subpoints:
            if not isinstance(proposed, dict):
                issues.append(module_id + '：重分组的小要点必须是对象')
                continue
            sid = proposed.get('id')
            if sid not in source_subpoints:
                issues.append(module_id + '：重分组引用未知小要点：' + str(sid))
                continue
            seen.append(sid)
            if sid in protected_subpoints:
                # Current/progress content stays attached to its old big point.
                continue
            owner, original, original_order = source_subpoints[sid]
            if owner not in requested_ids:
                issues.append(module_id + '：小要点来自未列入 replace_point_ids 的点：' + str(sid))
                continue
            subpoint = deepcopy(original)
            title = proposed.get('title')
            if isinstance(title, str) and title.strip():
                subpoint['title'] = title
            kept_subpoints.append((original_order, subpoint))
        pid = incoming.get('id')
        if not kept_subpoints:
            empty_ids.append(pid)
            continue
        kept_subpoints.sort(key=lambda item: item[0])
        # A subpoint id is scoped by its parent in the persisted store.  A
        # regroup changes that parent, so give each moved subpoint its new
        # parent-scoped id while retaining the old id in this mapping for
        # migrating question and classroom references.
        renamed = []
        transferred = {}
        transferred_rejections = []
        for index, (_, subpoint) in enumerate(kept_subpoints, 1):
            old_sid = subpoint['id']
            new_sid = f"{pid}-s{index}"
            subpoint = dict(subpoint, id=new_sid)
            # Keep an explicit alias on the child.  This lets preparation
            # carry terminal outcomes across a parent move even when the
            # source marker lived on the old parent record.
            aliases = subpoint.get('legacy_subpoint_ids', [])
            if isinstance(aliases, str): aliases = [aliases]
            if not isinstance(aliases, list): aliases = []
            subpoint['legacy_subpoint_ids'] = list(dict.fromkeys([*aliases, old_sid]))
            subpoint_renames[old_sid] = new_sid
            owner = source_subpoints[old_sid][0]
            marker = source_markers.get(owner, {})
            processed = marker.get('processed_subpoints', {}) if isinstance(marker, dict) else {}
            if isinstance(processed, list):
                processed = {r.get('subpoint_id'): r for r in processed if isinstance(r, dict)}
            if isinstance(processed, dict):
                item = processed.get(old_sid)
                if isinstance(item, str): item = {'status': item}
                if isinstance(item, dict) and item.get('status') in ('valid', 'rejected', 'retired'):
                    transferred[new_sid] = dict(item, subpoint_id=new_sid)
            if isinstance(marker, dict):
                for rejection in marker.get('rejections', []):
                    if isinstance(rejection, dict) and rejection.get('subpoint_id') == old_sid:
                        transferred_rejections.append(dict(rejection, subpoint_id=new_sid))
            renamed.append(subpoint)
        kept_subpoints = renamed
        point = deepcopy(incoming)
        point['subpoints'] = kept_subpoints
        if transferred or transferred_rejections:
            # Fingerprints intentionally remain invalidated after regroup;
            # _prepare_concept_point will refresh the marker while treating
            # these terminal child outcomes as already processed.
            existing_marker = point.get('subpoint_preparation', {})
            if not isinstance(existing_marker, dict): existing_marker = {}
            existing_processed = existing_marker.get('processed_subpoints', {})
            if isinstance(existing_processed, list):
                existing_processed = {r.get('subpoint_id'): r for r in existing_processed if isinstance(r, dict)}
            if not isinstance(existing_processed, dict): existing_processed = {}
            merged_processed = dict(existing_processed)
            merged_processed.update(transferred)
            point['subpoint_preparation'] = dict(existing_marker,
                state='pending', fingerprint='', result={},
                rejections=list(existing_marker.get('rejections', [])) + transferred_rejections,
                processed_subpoints=merged_processed)
        point['minutes'] = sum(s['minutes'] for s in kept_subpoints)
        point['chunk_ids'] = list(dict.fromkeys(cid for s in kept_subpoints for cid in s.get('chunk_ids', [])))
        cleaned.append(point)

    duplicates = sorted({sid for sid in seen if seen.count(sid) > 1})
    missing = sorted(expected - set(seen))
    if duplicates:
        issues.append(module_id + '：小要点重复分组：' + '、'.join(duplicates))
    if missing:
        issues.append(module_id + '：有未分组的小要点：' + '、'.join(missing))
    return cleaned, protected_subpoints, empty_ids, subpoint_renames


def _prepare(store, plan_id, structure, *, end_date=None):
    if not isinstance(structure, dict): raise StoreError('INVALID_STRUCTURE', '结构文件必须是 JSON 对象。')
    structure = deepcopy(structure)
    regroup = structure.get('regroup_subpoints') is True
    if 'regroup_subpoints' in structure and not isinstance(structure['regroup_subpoints'], bool):
        raise StoreError('INVALID_STRUCTURE', 'regroup_subpoints 必须是布尔值。')
    plan = store._get('plans', plan_id, plan_id)
    today = date.fromisoformat(structure.get('today') or date.today().isoformat())
    issues = []
    original_window = _window(plan['window'])
    requested_end = original_window.end
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
            requested_end = original_window.end
    new_window = replace(original_window, end=requested_end)
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
        concept_state = state.get('concept')
        stored_cursor = state.get('point_id') or (concept_state.get('point_id') if isinstance(concept_state, dict) else None)
        if stored_cursor and stored_cursor != cursor:
            issues.append('课堂 point_id 与模块/index 不一致，拒绝猜测当前进度')
    changed, replacement_ids, new_ids, seen_modules = [], set(), set(), set()
    question_moves, empty_regroup_groups, protected_subpoint_ids, regroup_details = [], [], set(), []
    subpoint_renames = {}
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
        requested_ids = set(ids)
        module_protected = {pid for pid in requested_ids if pid in protected}
        if regroup:
            # Preserve the active point even before it has a saved conversation.
            if cursor in requested_ids:
                module_protected.add(cursor)
                protected.setdefault(cursor, []).append('课堂当前位置（分组时保留）')
            # Questions without subpoint_id cannot be moved safely.
            for question in store.records('question_bank', plan_id):
                if not question.get('subpoint_id'):
                    tied = set(question.get('point_ids', [])) & requested_ids
                    module_protected.update(tied)
                    for pid in tied:
                        protected.setdefault(pid, []).append('题目没有 subpoint_id，保留原点以保留题目归属')
        elif module_protected:
            for pid in sorted(module_protected):
                issues.append('要取代的点已有进度，拒绝：' + pid + '（' + '、'.join(protected[pid]) + '）')
        actual_ids = requested_ids - module_protected if regroup else requested_ids
        replacement_ids.update(actual_ids)
        covered = sorted((points[pid] for pid in ids), key=lambda p: (p['position'], p['id']))
        indexes = [members.index(p) for p in covered]
        if not regroup and indexes != list(range(min(indexes), max(indexes) + 1)):
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
        if regroup:
            rows, protected_sids, emptied, renamed = _regroup_points(
                store, plan_id, covered, actual_ids, rows, module_protected, chunk_ids, issues, mid)
            subpoint_renames.update(renamed)
            protected_subpoint_ids.update(protected_sids)
            empty_regroup_groups.extend(str(pid) for pid in emptied if pid)
            regroup_details.append(dict(module_id=mid,
                preserved_point_ids=sorted(module_protected),
                preserved_subpoint_ids=sorted(protected_sids),
                empty_group_ids=[str(pid) for pid in emptied if pid]))
        first = min((points[pid]['position'] for pid in actual_ids), default=covered[0]['position'])
        prepared = []
        kept = [p for p in members if p['id'] not in actual_ids]
        positions = {p['position'] for p in kept}
        for i, incoming in enumerate(rows):
            if not isinstance(incoming, dict): issues.append(mid + '：新点必须是对象'); continue
            point = dict(incoming, module_id=mid,
                         position=(max((p['position'] for p in members), default=-1)+1+i) if regroup else first+i)
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
                       learning_minutes=sum(p['minutes'] for p in kept+prepared))
        if not regroup:
            updated.update(question_extraction=dict(state='pending', reason='restructure'),
                           material_preparation=dict(old_module.get('material_preparation') or {}, state='pending',
                               error=None, message='这个模块的题需要按资料重新准备，学习进度已保留。'))
        if not updated['point_count']: issues.append(mid+'：重切后没有可学习的点，现有课堂尚不能接续空模块')
        if not regroup and cursor in ids and not prepared: issues.append(mid+'：当前位置被取代，但没有可接续的新点')
        for nav in navigation:
            for cid in nav.get('chunk_ids', []) if isinstance(nav, dict) and isinstance(nav.get('chunk_ids'), list) else []:
                if isinstance(cid, str) and cid not in chunk_ids: issues.append('导航引用不存在的本计划 chunk：'+cid)
        try: store._validate('modules', plan_id, updated)
        except (StoreError, KeyError, TypeError, ValueError) as exc:
            issues.append('模块导航校验失败：' + mid + '（' + getattr(exc, 'code', type(exc).__name__) + '）')
        if actual_ids or (not regroup and ids):
            subpoint_targets = {sub['id']: point['id'] for point in prepared for sub in point.get('subpoints', [])}
            # A question may reference more than one point.  Only the parent
            # that actually owns its stable subpoint is replaced; other point
            # associations can refer to untouched content and must survive.
            subpoint_sources = {
                sub['id']: point['id']
                for point in store.records('points', plan_id)
                for sub in point.get('subpoints', [])
            }
            changed.append(dict(old=old_module, new=updated, points=prepared, replace=sorted(actual_ids),
                                requested=ids, subpoint_targets=subpoint_targets, regroup=regroup))
            if regroup:
                for question in store.records('question_bank', plan_id):
                    sid = question.get('subpoint_id')
                    new_sid = subpoint_renames.get(sid, sid)
                    target = subpoint_targets.get(new_sid)
                    if not target:
                        continue
                    old_question_points = question.get('point_ids', [])
                    if not set(old_question_points) & actual_ids:
                        continue
                    source = subpoint_sources.get(sid)
                    migrated = list(dict.fromkeys(target if pid == source else pid for pid in old_question_points))
                    if target not in migrated:
                        migrated.append(target)
                    question_moves.append(dict(id=question['id'], point_ids=migrated,
                                               subpoint_id=new_sid))
                    try:
                        store._validate('question_bank', plan_id,
                                        dict(question, point_ids=migrated, subpoint_id=new_sid), references=False)
                    except (StoreError, KeyError, TypeError, ValueError) as exc:
                        issues.append('题目无法按 subpoint_id 迁移：' + question['id'] +
                                      '（' + getattr(exc, 'code', type(exc).__name__) + '）')
    before_ledger = store._load_ledger(plan)
    total = plan['total_minutes'] + sum(c['new']['learning_minutes']-c['old']['learning_minutes'] for c in changed)
    after_ledger = Ledger.from_dict(before_ledger.to_dict())
    try:
        after_ledger.replan(today, new_window, total, preserve_past=True)
    except (StoreError, LedgerError, ValueError) as exc: issues.append('无法重排账本：' + str(exc))
    before, after = _calendar(before_ledger, today), _calendar(after_ledger, today)
    if any(before['targets'].get(day) != after['targets'].get(day) for day in before['targets'] if day < today.isoformat()):
        issues.append('历史目标发生变化，拒绝执行')
    questions = ([] if regroup else
                 [q for q in store.records('question_bank', plan_id)
                  if set(q['point_ids']) & replacement_ids and q.get('role') != 'retired'])
    journal = [tuple(row) for row in store._db.execute('SELECT entity,entity_id,payload,deleted,local_revision FROM _records WHERE plan_key=? ORDER BY entity,entity_id', (plan_id,))]
    token_input = [structure, journal, today.isoformat()]
    if end_date is not None: token_input.append(end_date)
    token = _digest(token_input)
    future = {day: minutes for day, minutes in after['targets'].items()
              if today.isoformat() <= day <= requested_end.isoformat()}
    daily_cap = new_window.daily_cap_minutes
    full_weight_days = [day for day in future if weight_of(new_window, date.fromisoformat(day)) == 1]
    capped_days = full_weight_days or list(future)
    over_cap = [dict(day=day, target=future[day], excess_minutes=future[day]-daily_cap)
                for day in capped_days if future[day] > daily_cap]
    allocation = after_ledger._allocation()
    outstanding = after['overdue_to_repay'] + sum(max(0, minutes
        - allocation.confirmed.get(date.fromisoformat(day), 0)
        - allocation.tentative.get(date.fromisoformat(day), 0)) for day, minutes in future.items())
    remaining_days = max(0, (requested_end-max(today, original_window.start)).days+1)
    future_study_days = sum(weight_of(new_window, date.fromisoformat(day)) > 0 for day in future)
    capacity = future_study_days * daily_cap
    daily_excess = sum(row['excess_minutes'] for row in over_cap)
    capacity_excess = max(0, outstanding-capacity)
    report = dict(plan_id=plan_id, title=plan.get('title') or plan['goal'], today=today.isoformat(),
                  can_apply=not issues, issues=issues, preview_token=token,
                  retained_old_points=len(points)-len(replacement_ids), protected_old_points=len(protected),
                  protected=protected, replaced_old_points=len(replacement_ids), new_points=len(new_ids),
                  new_subpoints=sum(len(p.get('subpoints', [])) for c in changed for p in c['points']),
                  navigation_entries=sum(len(c['new']['navigation']) for c in changed), retired_questions=len(questions),
                  regroup_subpoints=regroup, regrouped_questions=len(question_moves),
                  subpoint_renames=dict(subpoint_renames),
                  protected_subpoints=len(protected_subpoint_ids), empty_regroup_groups=empty_regroup_groups,
                  regroup_details=regroup_details,
                  classroom_redirect=cursor in replacement_ids,
                  modules=[dict(id=c['old']['id'], title=c['old']['title'], before=c['old']['learning_minutes'],
                                after=c['new']['learning_minutes'], before_points=c['old']['point_count'], after_points=c['new']['point_count'],
                                covered_before=sum(points[pid]['minutes'] for pid in c['replace']),
                                covered_after=sum(p['minutes'] for p in c['points'])) for c in changed],
                  total_before=plan['total_minutes'], total_after=total,
                  end_date_before=original_window.end.isoformat(), end_date_after=requested_end.isoformat(),
                  remaining_days=remaining_days,
                  deadline_change_supported=True,
                  daily_cap=dict(minutes=daily_cap, fits=not over_cap and not capacity_excess, over_cap_days=over_cap,
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
    return report, changed, questions, cursor, plan, question_moves


def apply_structure(store, plan_id, structure, *, dry_run, end_date=None):
    """Preview without writes; otherwise validate and commit all or nothing.

    end_date is optional YYYY-MM-DD. The total and window are applied in one
    normal store replan, retaining its immutable journal and FIFO debt.
    """
    with store.transaction():
        report, changed, questions, cursor, plan, question_moves = _prepare(store, plan_id, structure, end_date=end_date)
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
        for move in question_moves:
            question = store._get('question_bank', plan_id, move['id'])
            store._put('question_bank', plan_id, dict(question, point_ids=move['point_ids'],
                                                      subpoint_id=move.get('subpoint_id', question.get('subpoint_id'))))
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
        store.replan_plan(plan_id, date.fromisoformat(report['today']),
                      new_window=replace(_window(plan['window']), end=date.fromisoformat(report['end_date_after'])),
                      new_total_minutes=report['total_after'],
                      cause='restructure', reason='按已确认的知识点结构重排；预演 ' + report['preview_token'])
        return report


def format_report(report):
    lines = [f"计划：{report['title']}（{report['plan_id']}），按 {report['today']} 计算",
             '可执行' if report['can_apply'] else '不能执行：请先解决下面的问题',
             f"保留旧点 {report['retained_old_points']}（其中有进度/位于当前位置之前 {report['protected_old_points']}）；"
             f"取代 {report['replaced_old_points']}；新增大点 {report['new_points']}；资料导航 {report['navigation_entries']} 条（不计时、不考核）。",
             f"新大点包含 {report['new_subpoints']} 个小要点。",
             f"题库退役 {report['retired_questions']} 道；课堂位置{'将接到第一个新点' if report['classroom_redirect'] else '保持不变'}。"]
    if report.get('regroup_subpoints'):
        lines.append(f"保留原小要点 {report['protected_subpoints']} 个；空分组跳过 {len(report['empty_regroup_groups'])} 个；"
                     f"按小要点迁移题目 {report['regrouped_questions']} 道（题目内容与答题记录保留）。")
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
