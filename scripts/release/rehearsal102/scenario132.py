"""132 gate, using the existing real Inno/UIA/CDP/update drivers."""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from common import TASK, ROOT, APP_ID, environment
from native import controls, uia, wait_control
from scenario103 import snapshot, HERE
import scenario as s
import scenario107 as g

ENV = environment()
RESULT = TASK/'logs/result132.json'
sys.path.insert(0, str(ROOT))


def save(result):
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')


def boundary():
    for path in TASK.rglob('mentorflow.log'):
        if 'UAT_LOCAL_ONLY' in path.read_text(encoding='utf8', errors='replace'):
            raise RuntimeError('Network boundary violation; stopped: '+str(path))


def key_theme():
    s.cdp('click', '设置')
    # A fake model preset must also have a localhost endpoint. Classroom
    # prefetch is automatic now; never let it attempt a production model URL.
    s.cdp('evaluate', "[...document.querySelectorAll('label.field')].find(e=>e.querySelector('span')?.textContent==='接口地址').querySelector('input').setAttribute('data-uat-model-url','yes')")
    s.cdp('input', '[data-uat-model-url]', 'https://127.0.0.1:18764/anthropic')
    s.cdp('input', 'input[type=password]', 'sk-uat-not-a-real-deepseek-key-132')
    s.cdp('click', '保存并使用')
    s.cdp('wait-text', 'Key 已保存')
    assert s.cdp('evaluate', "[...document.querySelectorAll('label.field')].find(e=>e.querySelector('span')?.textContent==='接口地址').querySelector('input').value==='https://127.0.0.1:18764/anthropic'")
    s.cdp('click', '外观')
    s.cdp('evaluate', "[...document.querySelectorAll('.theme-card')].find(e=>e.textContent.includes('极光夜')).click()")
    boundary()


def seed(root, restructured=False):
    from datetime import date
    from refactor.desktop.account_identity import AccountIdentity
    from refactor.learning.course.store import CourseStore
    from refactor.learning.course.restructure import apply_structure
    from refactor.learning.course.question_bank import assessment_questions
    from refactor.learning.course.ledger import PlanWindow
    from tests.refactor.restructure114.test_restructure114_contract import proposal, START
    from tests.refactor.mastery70.support import seed_assessment
    # The store unit fixture uses 'p'; the real bridge requires a UUID-shaped
    # plan id. Preserve its data/relationships, using a valid UI identity.
    plan = '13200000000000000000000000000001'
    identity = AccountIdentity('https://localhost:18764/test-realm', 'test-update-user', '更新排练账号')
    with CourseStore(identity.account_id, root/'Data') as store:
        store.create_plan(plan, goal='离线重切验证', window=PlanWindow(START, date(2026,10,20), 600))
        store.add_sources(plan, [dict(id='src', kind='url',url='https://localhost:18764/book',title='资料',site='localhost',
            tier='official',credibility=1,adopted=True,chunks=[dict(chunk_id='c',text='依据正文',page_start=1,page_end=1,section=['章'])])])
        store.save_outline(plan, [dict(id='s',title='阶段')], [dict(id='m',stage_id='s',title='模块',prerequisites=[])])
        store.save_generated_module(plan,'m',[dict(id='old'+str(i),title='旧点'+str(i),summary='旧知识',minutes=20,
            chunk_ids=['c']) for i in range(6)],check_minutes=10,today=START)
        seed_assessment(store,plan)
        store.start_plan(plan)
        store.add_credit(plan,'credit0','old0',10,START,'confirmed')
        store.save_classroom_position(plan,dict(module_id='m',index=2,phase='learning',step='opening',in_session=False,
            completed_requests=['keep-request'],pending=None,test=None,check=None))
        if restructured:
            changes = dict(proposal(), plan_id=plan)
            preview = apply_structure(store, plan, changes, dry_run=True)
            assert preview['can_apply'], preview
            apply_structure(store, plan, changes, dry_run=False)
        # The fixture is already prepared; entering classroom must not ask a
        # real model to find questions. No teaching reply is injected.
        with store.transaction():
            module = store.records('modules', plan)[0]
            module['material_preparation'] = dict(state='done')
            module['question_count'] = len(assessment_questions(store.records('question_bank', plan)))
            store._put('modules', plan, module)
        value = dict(account=identity.account_id, points=store.records('points', plan),
                     plan=store.get_plan(plan), restructured=restructured)
        if restructured:
            # Replaced records remain in sync storage, deliberately absent
            # from the live points projection (including Store._get).
            value['replaced_records_preserved'] = [json.loads(row[0]) for row in store._db.execute(
                "SELECT payload FROM _records WHERE entity='points' AND plan_key=? "
                "AND json_type(payload,'$.replaced_by')='array'", (plan,))]
            assert len(value['replaced_records_preserved']) == 4, value
    return value


def detail(root, tag, restructured=False, classroom=False):
    s.cdp('click', '我的计划')
    s.cdp('wait-text', '离线重切验证')
    s.cdp('click', '查看路线')
    s.cdp('wait-text', '知识地图')
    s.cdp('click', '列表')
    s.cdp('evaluate', "document.querySelector('.module-node summary').click()")
    s.cdp('wait-text', '新概念' if restructured else '旧点0')
    text = s.cdp('evaluate', 'document.body.innerText')
    if restructured:
        assert '新概念' in text
        assert all('旧点'+str(i) not in text for i in range(2, 6)), text
    s.cdp('shot', str(TASK/f'screenshots/{tag}-detail.png'))
    value = dict(restructured=restructured, replaced_absent=restructured, detail_text=text)
    if classroom:
        s.cdp('click', '开课')
        s.cdp('wait-text', '展开知识点')
        s.cdp('click', '展开知识点')
        s.cdp('wait-text', '新概念' if restructured else '旧点0')
        s.cdp('wait-text', '开始上课')
        s.cdp('shot', str(TASK/f'screenshots/{tag}-classroom-before-teacher.png'))
        value['classroom_before_teacher'] = True
    boundary()
    return value


def retained():
    s.cdp('wait-text', '更新排练账号')
    s.cdp('click', '设置')
    s.cdp('wait-text', 'Key 已保存')
    assert s.cdp('evaluate', "document.documentElement.dataset.theme==='aurora'")
    boundary()
    return dict(key_retained=True, theme_retained=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--remaining', action='store_true', help='Retain the passed fresh gate; rerun cover/update after a driver correction')
    parser.add_argument('--cover-folder', default='user-final', help='Isolated fresh folder for a corrected cover replay')
    parser.add_argument('--verify-covered', action='store_true', help='Retain completed real cover evidence; reinstall P over retained task Data and resume UI verification')
    parser.add_argument('--cover-only', action='store_true', help='Replay the complete direct-cover path after driver fixes, retaining completed update gates')
    parser.add_argument('--updates-only', action='store_true', help='Retain passed fresh/cover UI gates; resume only real O/P/Q update paths')
    parser.add_argument('--update-folder', default='inapp', help='Isolated new folder for a corrected update replay')
    args = parser.parse_args()
    assert APP_ID == 'MentorFlow.UAT.gate132'
    for name in (args.cover_folder, args.update_folder):
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('replay folder must be one isolated folder name')
    result = g.read(RESULT) if args.remaining or args.verify_covered or args.updates_only else dict(baseline='386a926a', old_baseline='64fdf076', app_id=APP_ID)
    if args.remaining or args.verify_covered or args.updates_only:
        assert result.get('fresh_register_login_key_newplan'), result
        result.setdefault('driver_recoveries', []).append(result.pop('error', {}))
    if args.cover_only:
        assert args.remaining and not args.verify_covered
        assert result.get('old_to_P') and result.get('P_to_Q')
        result['intermediate_cover_ui'] = {key: result.pop(key) for key in
            ('cover_ui_reinstall', 'cover_ui_resumed_from') if key in result}
    if args.updates_only:
        assert not args.cover_only and not args.verify_covered
        assert result.get('cover_structure_ui', {}).get('classroom_before_teacher') and result.get('cover_second')
    result['complete'] = False
    before = snapshot('before')
    full_before = g.inventory('before')
    save(result)
    output = (TASK/'logs/servers.out').open('w', encoding='utf8')
    server = subprocess.Popen([sys.executable, str(HERE/'servers.py')], env=ENV, stdout=output, stderr=output)
    current = None
    try:
        if not (args.remaining or args.verify_covered or args.updates_only):
            fresh = TASK/'fresh/MentorFlow'; current = fresh; g.publish('P')
            result['fresh_setup'] = g.setup('P', fresh, 'fresh132'); save(result)
            began = time.time(); s.launch(fresh); wait_control(s.app_pid(fresh), '登录 MentorFlow', 45)
            result['fresh_login_seconds'] = round(time.time()-began, 3)
            windows = uia(s.app_pid(fresh), 'list')
            result['fresh_login_windows'] = windows
            title = next(row['name'] for row in windows if row.get('name') and not row.get('minimized'))
            uia(s.app_pid(fresh), 'shot', name=title, out=TASK/'screenshots/fresh132-login.png')
            s.register(fresh); s.login(fresh); key_theme()
            s.cdp('click', '新建计划'); s.cdp('wait-text', '想学什么')
            s.cdp('shot', str(TASK/'screenshots/fresh132-new-plan.png'))
            result['fresh_register_login_key_newplan'] = True; save(result)
            g.uninstall(fresh); current = None

        # User's route: old app used once, closed, then never opened again
        # before P overwrites it. Use the BUG-86 test fixture, not user data.
        if args.updates_only:
            pass
        elif args.verify_covered:
            assert result['cover_setup']['expected_upgrade_label'] and result['cover_data_unchanged']
            covered = Path(result['cover_setup']['default_path']).resolve()
            assert covered.is_relative_to(TASK.resolve())
            # Inno correctly refuses a previously uninstalled, nonempty folder.
            # The actual old->P cover already passed. Continue only its UI
            # checks in a fresh P install using the retained task Data copy.
            root = TASK/'cover-ui/MentorFlow'
            current = root; g.publish('P')
            result['cover_ui_reinstall'] = g.setup('P', root, 'cover-ui132')
            shutil.copytree(covered/'Data', root/'Data', dirs_exist_ok=True)
            result['cover_ui_resumed_from'] = str(covered)
        else:
            root = TASK/(args.cover_folder+'/MentorFlow' if args.remaining else 'user/MentorFlow'); current = root; g.publish('O')
            result['cover_old_setup'] = g.setup('O', root, 'cover-old132')
            s.launch(root); s.login(root); key_theme(); s.close(root)
            result['structure_fixture'] = seed(root, restructured=True)
            marker = root/'Data/uat-gate132.txt'; marker.write_text('gate132 learning stays', encoding='utf8')
            files = (marker, *list((root/'Data').rglob('course.sqlite3')))
            hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
            g.publish('P')
            result['cover_setup'] = g.setup('P', root, 'cover132', cover=True)
            assert result['cover_setup']['expected_upgrade_label'], result['cover_setup']
            result['cover_data_unchanged'] = all(hashlib.sha256((root/p).read_bytes()).hexdigest()==sha for p,sha in hashes.items())
            assert result['cover_data_unchanged']; save(result)
        if not args.updates_only:
            s.launch(root); s.login(root); result['cover_retained'] = retained()
            result['cover_structure_ui'] = detail(root, 'cover132', restructured=True, classroom=True)
            result['cover_check'] = s.run('check.py', root, '--version', '9.132.15')
            result['cover_second'] = g.second_start(root, '9.132.15', 'cover132'); save(result)
            g.uninstall(root); current = None
            if not args.verify_covered:
                result['cover_continuous_final'] = True

        if not args.cover_only:
            root = TASK/args.update_folder/'MentorFlow'; current = root; g.publish('O')
            result['inapp_old_setup'] = g.setup('O', root, 'inapp-old132')
            s.launch(root); s.login(root); key_theme(); s.close(root)
            result['old_plan_fixture'] = seed(root)
            s.launch(root); s.login(root)
            result['old_to_P'] = g.update(root, 'P', '9.132.15', 'old-P132'); save(result)
            result['existing_plan_ui'] = detail(root, 'old-P132')
            result['P_second'] = g.second_start(root, '9.132.15', 'P132'); save(result)
            s.login(root)
            result['background_Q_seconds'] = g.ready(root, 'Q', '9.132.16'); save(result)
            result['P_to_Q'] = g.update(root, 'Q', '9.132.16', 'P-Q132', offered=True); save(result)
            result['Q_second'] = g.second_start(root, '9.132.16', 'Q132'); save(result)
        else:
            result['cover_continuous_final'] = True
        boundary()
        result['complete'] = True
    except Exception as error:
        result['error'] = dict(type=type(error).__name__, detail=str(error))
        raise
    finally:
        if current and (current/'Maintenance/unins000.exe').is_file():
            try: g.uninstall(current)
            except Exception as error: result['uninstall_error'] = str(error)
        server.terminate(); server.wait(15); output.close()
        after = snapshot('after'); full_after = g.inventory('after')
        result['formal_full_inventory_unchanged'] = full_before == full_after
        result['formal_file_count'] = len(full_before['formalFiles'])
        result['default_location_unchanged'] = full_before['defaultFiles'] == full_after['defaultFiles']
        result['formal_files_unchanged'] = before == after
        result['registry_shortcuts_unchanged'] = g.read(TASK/'before.json') == g.read(TASK/'after.json')
        save(result)
    assert all(result.get(k) for k in ('complete', 'formal_full_inventory_unchanged',
        'formal_files_unchanged', 'registry_shortcuts_unchanged', 'default_location_unchanged')), result
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
