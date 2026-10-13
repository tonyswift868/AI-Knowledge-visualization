"""Offline plan restructuring. Preview first; --apply needs its confirmation token.

Examples (Python from the existing MentorFlow environment):
  plan_restructure.py --dry-run --data-root COPY --plan-id ID --structure apply-ready.json
  plan_restructure.py --apply ... --confirm-preview TOKEN
  plan_restructure.py --rollback --data-root COPY --plan-id ID --backup BACKUP

Rollback is local only by default. If the change has reached the server, first
finish its normal sync and exit the app; use --rollback --resync to enqueue the
old structure plus an immutable compensating replan, then let normal sync send
them. Never reopen a local-only rollback against an uncorrected remote copy.
No network, credentials, account registration or model calls occur here.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone, date, time
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from refactor.learning.course.store import CourseStore, StoreError, PARENTS, PROGRESS, _window
from refactor.learning.course.restructure import apply_structure, format_report, RestructureError
from refactor.learning.course.ledger import study_day


@contextmanager
def _read_database(path):
    """Only SQLite's backup API reads a database belonging to another owner."""
    memory = sqlite3.connect(':memory:')
    try:
        with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as source:
            source.backup(memory)
        yield memory
    finally:
        memory.close()


def business_snapshot(connection):
    return {entity: [json.loads(row[0]) for row in connection.execute(
        'SELECT payload FROM '+entity+' ORDER BY '+('id' if entity=='plans' else 'plan_key,id'))]
        for entity in PARENTS}


def business_digest(connection):
    return hashlib.sha256(json.dumps(business_snapshot(connection), sort_keys=True,
                                   ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def make_backup(store):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path = store.path.with_name('course.sqlite3.before-restructure-'+stamp+'.bak')
    with store._lock, closing(sqlite3.connect(path)) as destination:
        store._db.backup(destination)
    return path


def _manifest_path(backup):
    return Path(str(backup)+'.json')


def execute(store, plan_id, structure, confirmation, *, end_date=None):
    preview = apply_structure(store, plan_id, structure, dry_run=True, end_date=end_date)
    if preview['issues']: raise RestructureError(preview)
    if confirmation != preview['preview_token']:
        raise StoreError('CONFIRM_PREVIEW', '必须先看预演，再传 --confirm-preview 预演编号；数据或日期变化后请重新预演。')
    backup = make_backup(store)
    # The app's instance lock is held by main. This transaction also fences any
    # unexpected local writer between preview, backup and commit.
    with store.transaction():
        checked = apply_structure(store, plan_id, structure, dry_run=True, end_date=end_date)
        if checked['preview_token'] != confirmation:
            raise StoreError('STALE_PREVIEW', '备份期间数据变了，未执行，请重新预演。')
        report = apply_structure(store, plan_id, structure, dry_run=False, end_date=end_date)
        manifest = dict(version=1, account_id=store.account_id, plan_id=plan_id, database=str(store.path.resolve()),
                        backup=str(backup.resolve()), preview=report, after_digest=business_digest(store._db))
        _manifest_path(backup).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return report, backup


def rollback(data_root, account_id, plan_id, backup, *, resync=False):
    backup = Path(backup).resolve()
    manifest = json.loads(_manifest_path(backup).read_text(encoding='utf-8'))
    with CourseStore(account_id, data_root) as store:
        if (manifest['plan_id'] != plan_id or manifest['account_id'] != account_id
                or Path(manifest['database']) != store.path.resolve()):
            raise StoreError('BACKUP_SCOPE', '备份不是这个账号/计划/课程库的备份，未恢复。')
        if business_digest(store._db) != manifest['after_digest']:
            raise StoreError('ROLLBACK_HAS_NEW_ACTIVITY', '执行后课程库已有其他变化，整库回滚会覆盖新进度，已拒绝；请交架构师处理。')
        with _read_database(backup) as original:
            if resync:
                return _queue_rollback(store, original, manifest)
            old = {(r[0],r[1]): (r[2], bool(r[3]), r[4]) for r in original.execute(
                'SELECT entity,entity_id,payload,deleted,server_revision FROM _records')}
            for row in store._db.execute('SELECT * FROM _records'):
                before = old.get((row['entity'],row['entity_id']))
                changed = before is None or (row['payload'],bool(row['deleted'])) != before[:2]
                if changed and row['server_revision'] != (before[2] if before else 0):
                    raise StoreError('ROLLBACK_NEEDS_RESYNC', '已有重切变化确认上传，不能仅恢复本机；请用 --rollback --resync。')
    # No CourseStore connection is alive while the database is restored.
    path = Path(manifest['database'])
    with closing(sqlite3.connect(backup.as_uri()+'?mode=ro', uri=True)) as source, closing(sqlite3.connect(path)) as destination:
        source.backup(destination)
        destination.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    return dict(local_restored=True, queued_for_sync=False,
                warning='仅恢复本机。若改动已同步，不要直接启动软件；应在回滚前用 --rollback --resync 排队补偿同步。')


def _queue_rollback(store, original, manifest):
    """Restore business values using normal writes and current acknowledged CAS.

    Original progress facts and the already-published restructure event cannot
    be erased on the server. A second replan compensates it at the same boundary.
    Point inactivity is reversible; no permanent server tombstone is created.
    """
    old_records = {(r[0], r[1]): (r[2], json.loads(r[3]), bool(r[4])) for r in original.execute(
        'SELECT entity,entity_id,plan_key,payload,deleted FROM _records')}
    current = list(store._db.execute('SELECT * FROM _records'))
    changes = [r for r in current if (r['entity'], r['entity_id']) not in old_records or
               (r['payload'], bool(r['deleted'])) != (
                   json.dumps(old_records[(r['entity'], r['entity_id'])][1], ensure_ascii=False, sort_keys=True, separators=(',', ':')),
                   old_records[(r['entity'], r['entity_id'])][2])]
    if any(r['pending'] for r in changes):
        raise StoreError('ROLLBACK_SYNC_NOT_ACKED', '改动尚未全部确认同步。若从未同步可用本机回滚；若可能已发出，请先让软件同步完成再退出，用 --resync 回滚。')
    plan_id = manifest['plan_id']
    desired = business_snapshot(original)
    rollback_points = {}
    for row in current:
        if row['entity'] == 'points' and not row['deleted']:
            payload = json.loads(row['payload'])
            for pid in payload.get('replaced_by', []):
                rollback_points.setdefault(pid, []).append(payload['id'])
    with store.transaction():
        for entity in PARENTS:
            if entity in PROGRESS: continue
            for key, (scope, payload, deleted) in old_records.items():
                if key[0] == entity and not deleted:
                    store._put(entity, scope, {k:v for k,v in payload.items() if k != '_field_versions'})
        for entity in reversed(tuple(PARENTS)):
            if entity in PROGRESS: continue
            for row in current:
                key = (entity, row['entity_id'])
                if row['entity'] == entity and not row['deleted'] and (key not in old_records or old_records[key][2]):
                    payload = {k:v for k,v in json.loads(row['payload']).items() if k != '_field_versions'}
                    if entity == 'points':
                        store._put(entity, row['plan_key'], dict(payload, replaced_by=rollback_points.get(payload['id'], []),
                            replacement_operation='rollback:'+manifest['preview']['preview_token']))
                    else:
                        store._delete(entity, row['plan_key'], payload['id'])
        plan = next(p for p in desired['plans'] if p['id'] == plan_id)
        store._replan(plan, date.fromisoformat(manifest['preview']['today']), _window(plan['window']), plan['total_minutes'],
                      cause='restructure', reason='回滚已同步的重切：'+manifest['preview']['preview_token'])
        store._put('plans', plan_id, plan)
    return dict(local_restored=True, queued_for_sync=True,
                warning='旧结构与补偿账本已排队；现在启动软件完成正常同步。其他设备也需使用支持 concept_v1/restructure 的版本。')


def _running_software():
    if sys.platform != 'win32': return []
    probe = r"""Get-CimInstance Win32_Process | Where-Object {
      $_.Name -match '^MentorFlow.*[.]exe$' -or
      ($_.Name -match '^pythonw?[.]exe$' -and $_.CommandLine -match 'refactor[.]desktop[.]account_shell_main|account_shell_main[.]py')
    } | Select-Object ProcessId,Name | ConvertTo-Json -Compress"""
    result = subprocess.run(['powershell', '-NoProfile', '-Command', probe], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode != 0: raise StoreError('PROCESS_CHECK_FAILED', '无法确认软件已退出，未操作数据库。')
    value = json.loads(result.stdout.strip() or '[]')
    return value if isinstance(value, list) else [value]


def _account(data_root, plan_id, specified=None):
    base = Path(data_root)/'learning/accounts'
    choices = [base/specified/'course.sqlite3'] if specified else sorted(base.glob('*/course.sqlite3'))
    found = []
    for path in choices:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            if db.execute('SELECT 1 FROM plans WHERE id=?', (plan_id,)).fetchone(): found.append(path.parent.name)
    if len(found) != 1: raise StoreError('ACCOUNT_AMBIGUOUS', '找不到唯一账号中的这个计划，请提供 --account-id。')
    return found[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    for option in ('dry-run', 'apply', 'rollback'): mode.add_argument('--'+option, action='store_true')
    parser.add_argument('--data-root', required=True, type=Path)
    parser.add_argument('--plan-id', required=True)
    parser.add_argument('--account-id')
    parser.add_argument('--structure', type=Path)
    parser.add_argument('--today', help='明确学习日 YYYY-MM-DD；默认本机今天')
    parser.add_argument('--end-date', help='新截止日 YYYY-MM-DD；现有账本暂仅支持日期变更预演')
    parser.add_argument('--confirm-preview')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--resync', action='store_true', help='已确认上传过的结构：回滚业务值并排队补偿同步')
    parser.add_argument('--report', type=Path, help='保存完整 JSON 预演/执行回执')
    args = parser.parse_args(argv)
    lock = None
    try:
        running = _running_software()
        if running: raise StoreError('APP_RUNNING', '软件仍在运行，请自行退出后再操作：'+json.dumps(running))
        from PySide6.QtCore import QLockFile
        lock_path = args.data_root/'runtime/MentorFlow.instance.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(lock_path)); lock.setStaleLockTime(0)
        if not lock.tryLock(0): raise StoreError('APP_RUNNING', '软件实例锁仍被占用，未操作数据库。')
        account = _account(args.data_root, args.plan_id, args.account_id)
        if args.rollback:
            if args.end_date: raise StoreError('INVALID_ARGUMENT', '--end-date 只用于 --dry-run/--apply。')
            if not args.backup: raise StoreError('BACKUP_REQUIRED', '回滚必须指定 --backup。')
            output = rollback(args.data_root, account, args.plan_id, args.backup, resync=args.resync)
            print(json.dumps(output, ensure_ascii=False, indent=2))
        else:
            if not args.structure: raise StoreError('STRUCTURE_REQUIRED', '必须指定 --structure。')
            structure = json.loads(args.structure.read_text(encoding='utf-8'))
            from refactor.learning.app.settings import AppSettings
            settings = AppSettings(account, args.data_root).load()
            current_day = study_day(datetime.now(timezone.utc), time.fromisoformat(settings.get('refresh_time','04:00')),
                                    datetime.now().astimezone().tzinfo).isoformat()
            structure['today'] = args.today or structure.get('today') or current_day
            with CourseStore(account, args.data_root) as store:
                if args.dry_run: output = apply_structure(store, args.plan_id, structure, dry_run=True, end_date=args.end_date)
                else:
                    output, backup = execute(store, args.plan_id, structure, args.confirm_preview, end_date=args.end_date)
                    output = dict(output, backup=str(backup), local_only_rollback_command='--rollback --backup '+str(backup))
            print(format_report(output))
        if args.report: args.report.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if output.get('can_apply', True) else 2
    except RestructureError as exc:
        print(format_report(exc.report), file=sys.stderr); return 2
    except (StoreError, ValueError, OSError, sqlite3.Error) as exc:
        print(getattr(exc, 'code', type(exc).__name__)+': '+str(exc), file=sys.stderr); return 2
    finally:
        if lock: lock.unlock()


if __name__ == '__main__':
    raise SystemExit(main())
