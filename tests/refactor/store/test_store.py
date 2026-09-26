import copy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from refactor.learning.course.ledger import Ledger, PlanWindow
from refactor.learning.course.mastery import MasteryBook, PointRef, Judgement, AttemptClosed
from refactor.learning.course.store import CourseStore, StoreError, SCHEMA_VERSION, MAX_PAYLOAD
from refactor.learning.course.sync_contract import RecordStore, SyncRecord

TODAY = date(2026, 9, 27)
NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
WINDOW = PlanWindow(TODAY, TODAY + timedelta(days=4), 100)
SCRATCH = Path('E:/Codex/Temp/MentorFlowTests/course-store')


class StoreTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.root = Path(self.temp.name)
        self.stores = []
        self.store = self.open()

    def tearDown(self):
        for store in reversed(self.stores): store.close()
        self.temp.cleanup()

    def open(self, account='account', root=None):
        store = CourseStore(account, root or self.root)
        self.stores.append(store)
        return store

    def prepare(self, store=None, plan='p', modules=2):
        s = store or self.store
        s.create_plan(plan, goal='学习目标', final_goal='达到目标', level='入门', notes='备注', window=WINDOW)
        s.add_sources(plan, [dict(id='source', kind='url', url='https://example.invalid/paper', title='资料', site='大学',
                                  tier='official', credibility=0.95, adopted=True,
                                  chunks=[dict(chunk_id='chunk', text='依据正文', page_start=1, page_end=2, section=['章节'])])])
        s.save_outline(plan, [dict(id='stage', title='阶段')],
                       [dict(id=f'm{i}', stage_id='stage', title=f'模块{i}', prerequisites=['m0'] if i else []) for i in range(modules)])
        for i in range(modules):
            s.save_generated_module(plan, f'm{i}', [dict(id=f'point{i}', summary=f'知识点{i}', minutes=40, chunk_ids=['chunk'])], check_minutes=10, today=TODAY)
        return s

    def complete(self, store=None, plan='p'):
        s = store or self.store
        s.start_plan(plan)
        for i, module in enumerate(s.records('modules', plan)):
            seq = 10 * (i + 1)
            point = f'point{i}'
            s.append_judgement(plan, Judgement(f'j{i}', seq, NOW, 'module_test', module['id'], f'a{i}', ((point, True),)))
            s.append_attempt_closed(plan, AttemptClosed(f'ac{i}', seq + 1, module['id'], f'a{i}', 'module_test'))
        s.start_checking(plan)
        for i, module in enumerate(s.records('modules', plan)):
            s.append_judgement(plan, Judgement(f'check{i}', 100 + i, NOW, 'check', module['id'], results=((f'point{i}', True),), angle='recall'))
        s.add_credit(plan, 'done', 'all', s.get_plan(plan)['total_minutes'], TODAY, 'confirmed')
        s.complete_plan(plan, today=TODAY)
        return s

    def snapshot(self, store=None):
        s = store or self.store
        return '\n'.join(s._db.iterdump())

    def test_database_schema_wal_foreign_keys_and_reopen(self):
        s = self.prepare()
        self.assertEqual(s.path, self.root/'learning/accounts/account/course.sqlite3')
        self.assertEqual(s._db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        self.assertEqual(s._db.execute('PRAGMA foreign_keys').fetchone()[0], 1)
        self.assertEqual(s._db.execute('SELECT version FROM schema_version').fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(s._db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertTrue(isinstance(s, RecordStore))
        reopened = self.open()
        self.assertEqual(reopened.get_plan('p'), s.get_plan('p'))
        self.assertEqual(reopened.load_ledger('p').to_dict(), s.load_ledger('p').to_dict())

    def test_upgrade_keeps_rows_events_outbox_and_cursor(self):
        s = self.prepare(); s.set_sync_cursor('kept')
        pending = s.pending_records(100); ledger = s.load_ledger('p').to_dict()
        s._db.execute('DROP INDEX records_pending')
        s._db.execute('DROP INDEX records_plan')
        s._db.execute('DROP INDEX messages_order')
        s._db.execute('UPDATE schema_version SET version=1')
        upgraded = self.open()
        self.assertEqual(upgraded._db.execute('SELECT version FROM schema_version').fetchone()[0], 2)
        self.assertEqual(upgraded.sync_cursor(), 'kept')
        self.assertEqual(upgraded.pending_records(100), pending)
        self.assertEqual(upgraded.load_ledger('p').to_dict(), ledger)

    def test_unknown_schema_and_account_mismatch_do_not_overwrite(self):
        s = self.prepare(); s._db.execute('UPDATE schema_version SET version=99')
        before = self.snapshot()
        with self.assertRaises(StoreError): self.open()
        self.assertEqual(self.snapshot(), before)
        s._db.execute('UPDATE schema_version SET version=2'); s.close()
        other = self.root/'learning/accounts/other'; other.mkdir()
        shutil.copyfile(s.path, other/'course.sqlite3')
        with self.assertRaises(StoreError): self.open('other')

    def test_intake_sources_generated_data_and_provenance(self):
        s = self.prepare()
        s.save_intake('p', goal='修改目标', notes='补充')
        self.assertEqual(s.get_plan('p')['state'], 'ready')
        self.assertEqual(s.get_plan('p')['goal'], '修改目标')
        s.set_source_adopted('p', 'source', False)
        self.assertFalse(s.records('sources', 'p')[0]['adopted'])
        self.assertEqual(s.records('source_chunks', 'p')[0]['id'], 'chunk')
        self.assertEqual(s._db.execute('SELECT COUNT(*) FROM point_sources').fetchone()[0], 2)
        before = self.snapshot()
        for points in ([dict(id='bad', summary='无来源', minutes=1, chunk_ids=[])],
                       [dict(id='bad', summary='来源不存在', minutes=1, chunk_ids=['missing'])]):
            with self.assertRaises(StoreError): s.save_generated_module('p', 'm0', points, check_minutes=1, today=TODAY)
            self.assertEqual(self.snapshot(), before)

    def test_transactions_roll_back_mid_batch_and_deferred_foreign_keys(self):
        s = self.prepare(); before = self.snapshot()
        with self.assertRaises(StoreError):
            s.add_sources('p', [dict(id='first', kind='url', url='https://example.invalid'), dict(id='bad', kind='file', sha256='bad')])
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(StoreError):
            s.save_outline('p', [dict(id='newstage')], [dict(id='newmodule', stage_id='newstage', prerequisites=['newmodule'])])
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(sqlite3.IntegrityError):
            with s.transaction():
                s._put('points', 'p', dict(id='fk', module_id='missing', position=0, summary='fk', minutes=1, chunk_ids=['chunk']))
        self.assertEqual(self.snapshot(), before)

    def test_reorder_delete_and_explicit_replan_match_t03(self):
        s = self.prepare(); expected = s.load_ledger('p')
        s.reorder_modules('p', ['m1', 'm0'], today=TODAY)
        expected.replan(TODAY, WINDOW, 100)
        self.assertEqual(s.load_ledger('p').to_dict(), expected.to_dict())
        s.delete_module('p', 'm0', today=TODAY)
        expected.replan(TODAY, WINDOW, 50)
        self.assertEqual(s.load_ledger('p').to_dict(), expected.to_dict())
        self.assertEqual(s.records('modules', 'p')[0]['prerequisites'], [])
        self.assertEqual([p['id'] for p in s.records('points', 'p')], ['point1'])
        new_window = PlanWindow(TODAY, TODAY+timedelta(days=7), 100)
        s.replan_plan('p', TODAY+timedelta(days=1), new_window)
        expected.replan(TODAY+timedelta(days=1), new_window, 50)
        self.assertEqual(s.load_ledger('p').to_dict(), expected.to_dict())
        self.assertEqual(s.get_plan('p')['window']['end'], new_window.end.isoformat())

    def test_failed_delete_replan_is_atomic(self):
        s = self.prepare(); s.add_credit('p', 'old', 'u', 100, TODAY, 'confirmed')
        before = self.snapshot()
        with self.assertRaises(ValueError): s.delete_module('p', 'm0', today=TODAY+timedelta(days=1))
        self.assertEqual(self.snapshot(), before)

    def test_started_plan_cannot_reorder_delete_or_rewrite_points(self):
        s = self.prepare(); s.start_plan('p'); before = self.snapshot()
        for action in [lambda: s.delete_module('p', 'm0', today=TODAY), lambda: s.reorder_modules('p', ['m1','m0'], today=TODAY),
                       lambda: s.save_generated_module('p', 'm0', [dict(id='point0', summary='改', minutes=1, chunk_ids=['chunk'])], check_minutes=1, today=TODAY)]:
            with self.assertRaises(StoreError): action()
            self.assertEqual(self.snapshot(), before)

    def test_started_plan_can_append_new_content_without_rewriting_history(self):
        s = self.prepare(); s.start_plan('p'); s.add_credit('p','prior','u',30,TODAY,'confirmed')
        expected = s.load_ledger('p')
        s.save_outline('p', [], [dict(id='new', stage_id='stage', position=2, prerequisites=['m0'])])
        s.save_generated_module('p','new',[dict(id='newpoint', summary='新增', minutes=10, chunk_ids=['chunk'])],check_minutes=5,today=TODAY+timedelta(days=1))
        expected.replan(TODAY+timedelta(days=1),WINDOW,115)
        self.assertEqual(s.load_ledger('p').to_dict(), expected.to_dict())
        self.assertEqual(s.get_plan('p')['state'],'active')

    def test_credit_confirm_revoke_idempotency_and_replay(self):
        s = self.prepare(); expected = s.load_ledger('p')
        s.add_credit('p','c1','u',30,TODAY); expected.add_tentative('c1','u',30,TODAY)
        s.add_credit('p','c2','v',20,TODAY,'confirmed'); expected.add_confirmed('c2','v',20,TODAY)
        self.assertEqual(s.confirm_credits('p','u',event_id='confirm'),expected.confirm('u'))
        s.add_credit('p','c3','w',10,TODAY); expected.add_tentative('c3','w',10,TODAY)
        self.assertEqual(s.revoke_credits('p','w',event_id='revoke'),expected.revoke('w'))
        before = self.snapshot()
        s.add_credit('p','c3','w',10,TODAY)
        self.assertEqual(s.confirm_credits('p','u',event_id='confirm'),0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(s.load_ledger('p').to_dict(),expected.to_dict())
        for offset in range(7): self.assertEqual(s.load_ledger('p').status(TODAY+timedelta(days=offset)),expected.status(TODAY+timedelta(days=offset)))
        with self.assertRaises(ValueError): s.add_credit('p','c1','u',31,TODAY)
        with self.assertRaises(StoreError): s.revoke_credits('p','u',event_id='confirm')

    def test_mastery_replay_preserves_original_events_and_details(self):
        s = self.prepare(modules=1); expected = MasteryBook([PointRef('point0','m0')])
        events = [Judgement('j',1,NOW,'module_test','m0','a',(('point0',True),)), AttemptClosed('close',2,'m0','a','module_test'),
                  Judgement('check',3,NOW,'check','m0',results=(('point0',True),),angle='recall')]
        for event in reversed(events):
            if isinstance(event,Judgement): s.append_judgement('p',event,details={'question':'问题','answer':'回答','chunk_ids':['chunk']})
            else: s.append_attempt_closed('p',event)
            expected.apply(event)
        actual=s.load_mastery('p')
        self.assertEqual(actual.to_dict(),expected.to_dict())
        self.assertEqual(actual.stats(),expected.stats())
        self.assertTrue(actual.check_complete())
        self.assertEqual(s.records('judgements','p')[0]['details']['answer'],'回答')

    def test_messages_compression_profiles_reports_and_archive(self):
        s=self.prepare(); s.save_teacher_profile('p',{'progress':'原档案'})
        s.append_message('p','m0','user','问题',message_id='one',at=NOW)
        s.append_message('p','m0','assistant','回答',message_id='two',at=NOW)
        before=self.snapshot()
        with self.assertRaises(StoreError): s.compress_messages('p','m0',['one','two'],'x'*(MAX_PAYLOAD+1),summary_id='bad')
        self.assertEqual(self.snapshot(),before)
        s.compress_messages('p','m0',['one','two'],'摘要',summary_id='summary',at=NOW)
        self.assertEqual([m['id'] for m in s.messages('p','m0')],['summary'])
        self.assertEqual(len(s.messages('p','m0',include_compressed=True)),3)
        with self.assertRaises(StoreError): s.save_report('p',{'early':True})
        self.complete(s); s.save_report('p',{'report':'完成画像'})
        s.archive_plan('p'); self.assertEqual(s.list_plans(),[])
        self.assertEqual(len(s.records('reports','p')),1)
        s.restore_plan('p'); self.assertEqual(len(s.list_plans()),1)
        pending_events=[r for r in s.pending_records(999) if r.entity in ('credits','ledger_events','judgements','attempt_closed')]
        s.delete_plan('p')
        self.assertEqual(s.records('reports','p'),[])
        with self.assertRaises(StoreError): s.get_plan('p')
        self.assertTrue(all(r in s.pending_records(999) for r in pending_events))
        self.assertTrue(any(r.entity=='reports' and r.deleted and 'document' not in r.payload for r in s.pending_records(999)))

    def test_chat_usage_originals_and_account_isolation(self):
        s=self.prepare(); session=s.create_chat_session('闲聊',session_id='chat')
        s.rename_chat_session(session,'新标题'); s.append_chat_message(session,'user','你好',message_id='chat-one',at=NOW)
        self.assertEqual(s.chat_messages(session)[0]['content'],'你好')
        s.append_usage('p','usage',provider='p',model='m',purpose='teacher',input_tokens=10,cache_hit_tokens=8,cache_miss_tokens=2,output_tokens=3)
        before=s.pending_records(999)
        digest=s.put_original(b'original'); self.assertEqual(s.get_original(digest),b'original')
        self.assertEqual(s.put_original(b'original'),digest)
        with self.assertRaises(StoreError): s.put_original(b'wrong',digest)
        self.assertEqual(s.pending_records(999),before)
        other=self.open('other'); self.assertIsNone(other.get_original(digest)); self.assertEqual(other.list_plans(),[])
        self.assertEqual(other.pending_records(10),[])
        s.delete_chat_session(session); self.assertEqual(s.chat_messages(session),[])
        legacy=s.root/'plans.sqlite3'; legacy.write_bytes(b'legacy untouched')
        self.open(); self.assertEqual(legacy.read_bytes(),b'legacy untouched')

    def copy_remote(self, source, target, revision=1, records=None):
        records=source.pending_records(10000) if records is None else records
        target.apply_remote([(r, revision) for r in records])
        return records

    def test_sync_full_round_trip_has_no_upload_echo(self):
        source=self.prepare(); self.complete(source)
        source.save_report('p',{'complete':True}); source.save_teacher_profile('p',{'weaknesses':['概念']})
        target=self.open(root=self.root/'second-device')
        records=self.copy_remote(source,target)
        self.assertEqual(target.get_plan('p'),source.get_plan('p'))
        self.assertEqual(target.load_ledger('p').to_dict(),source.load_ledger('p').to_dict())
        self.assertEqual(target.load_mastery('p').to_dict(),source.load_mastery('p').to_dict())
        self.assertEqual(target.records('reports','p'),source.records('reports','p'))
        self.assertEqual(target.pending_records(1000),[])
        for record in records:
            self.assertEqual(record.schema,f'mentorflow.course.{record.entity}.v1')
            raw=json.dumps(record.payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
            self.assertLessEqual(len(raw),MAX_PAYLOAD)
            source.mark_synced(record.entity,record.entity_id,record.local_revision,1)
        self.assertEqual(source.pending_records(1000),[])

    def test_ack_race_and_old_echo_do_not_clear_or_replace_new_edit(self):
        s=self.prepare(); old=next(r for r in s.pending_records(999) if r.entity=='plans')
        s.save_intake('p',goal='更晚修改')
        newer=next(r for r in s.pending_records(999) if r.entity=='plans')
        self.assertGreater(newer.local_revision,old.local_revision)
        s.mark_synced(old.entity,old.entity_id,old.local_revision,10)
        self.assertIn(newer,s.pending_records(999))
        s.apply_remote([(old,10)])
        self.assertEqual(s.get_plan('p')['goal'],'更晚修改')
        s.mark_synced(newer.entity,newer.entity_id,newer.local_revision,11)
        self.assertFalse(any(r.entity=='plans' for r in s.pending_records(999)))

    def test_remote_structure_lww_but_progress_is_deduplicated(self):
        s=self.prepare(); s.add_credit('p','local','u',10,TODAY,'confirmed')
        remote=next(r for r in s.pending_records(999) if r.entity=='plans')
        payload=dict(remote.payload,goal='远端覆盖')
        s.apply_remote([(replace(remote,payload=payload),20)])
        self.assertEqual(s.get_plan('p')['goal'],'远端覆盖')
        s.apply_remote([(remote,19)])
        self.assertEqual(s.get_plan('p')['goal'],'远端覆盖')
        credit=next(r for r in s.pending_records(999) if r.entity=='credits')
        before=s.load_ledger('p').to_dict()
        s.apply_remote([(credit,21),(credit,21),(replace(credit,deleted=True),22)])
        self.assertEqual(s.load_ledger('p').to_dict(),before)
        self.assertIn(credit,s.pending_records(999))
        bad=copy.deepcopy(credit.payload); bad['credit']['minutes']+=1
        snapshot=self.snapshot()
        with self.assertRaises(StoreError): s.apply_remote([(replace(credit,payload=bad),23)])
        self.assertEqual(self.snapshot(),snapshot)

    def test_progress_from_two_devices_merges_without_rewriting_events(self):
        a=self.prepare(); b=self.open(root=self.root/'device-b'); self.copy_remote(a,b)
        a.add_credit('p','a-credit','u',10,TODAY,'confirmed')
        b.add_credit('p','b-credit','v',15,TODAY)
        ra=next(r for r in a.pending_records(999) if r.entity=='credits')
        rb=next(r for r in b.pending_records(999) if r.entity=='credits')
        a.apply_remote([(rb,5)]); b.apply_remote([(ra,5)])
        self.assertEqual(a.load_ledger('p').to_dict(),b.load_ledger('p').to_dict())
        self.assertEqual(a.load_ledger('p').status(TODAY).remaining_minutes,75)
        a.add_credit('p','a-credit','u',10,TODAY,'confirmed')
        self.assertIn(ra,a.pending_records(999))

    def test_parentless_remote_records_are_retained_until_parents_arrive(self):
        source=self.prepare(); target=self.open(root=self.root/'target')
        records=source.pending_records(999)
        children=[r for r in records if r.entity in ('points','source_chunks','modules','ledger_events')]
        parents=[r for r in records if r not in children]
        target.apply_remote([(r,1) for r in children])
        self.assertEqual(target.records('points','p'),[])
        target.set_sync_cursor('children-retained')
        target.apply_remote([(r,2) for r in reversed(parents)])
        self.assertEqual(target.load_ledger('p').to_dict(),source.load_ledger('p').to_dict())
        self.assertEqual(target.records('points','p'),source.records('points','p'))
        self.assertEqual(target._db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_remote_plan_deletion_keeps_unsent_progress(self):
        s=self.prepare(); s.add_credit('p','pending','u',10,TODAY)
        pending=next(r for r in s.pending_records(999) if r.entity=='credits')
        plan=next(r for r in s.pending_records(999) if r.entity=='plans')
        s.apply_remote([(replace(plan,payload={'id':'p'},deleted=True),100)])
        self.assertEqual(s.list_plans(),[])
        self.assertIn(pending,s.pending_records(999))
        s.apply_remote([(replace(pending,deleted=True),101)])
        self.assertIn(pending,s.pending_records(999))

    def test_remote_batch_and_cursor_roll_back_together(self):
        source=self.prepare(); target=self.open(root=self.root/'target')
        records=source.pending_records(999)
        before=self.snapshot(target)
        with self.assertRaises(StoreError):
            with target.transaction():
                target.set_sync_cursor('should-not-stick')
                target.apply_remote([(records[0],1),(replace(records[1],schema='future.v9'),2)])
        self.assertEqual(self.snapshot(target),before)
        with target.transaction():
            target.apply_remote([(r,1) for r in records]); target.set_sync_cursor('done')
        self.assertEqual(target.sync_cursor(),'done')

    def test_remote_invalid_ids_size_and_fk_scope_are_rejected(self):
        s=self.prepare(); record=next(r for r in s.pending_records(999) if r.entity=='plans')
        before=self.snapshot()
        bads=[replace(record,entity='unknown'),replace(record,entity_id='other'),
              replace(record,plan_key='other'),replace(record,payload=dict(record.payload,notes='字'*MAX_PAYLOAD)),
              replace(record,payload=dict(record.payload,archived='yes'))]
        for bad in bads:
            with self.subTest(bad=bad.entity):
                with self.assertRaises((StoreError,ValueError,TypeError)): s.apply_remote([(bad,10)])
                self.assertEqual(self.snapshot(),before)

    def test_sync_deletion_of_report_and_chat(self):
        source=self.prepare(); self.complete(source); source.save_report('p',{'r':'data'})
        source.create_chat_session('title',session_id='s'); source.append_chat_message('s','user','text',message_id='msg',at=NOW)
        target=self.open(root=self.root/'target'); records=self.copy_remote(source,target)
        for r in records: source.mark_synced(r.entity,r.entity_id,r.local_revision,1)
        source.delete_plan('p'); source.delete_chat_session('s')
        target.apply_remote([(r,2) for r in source.pending_records(999)])
        self.assertEqual(target.list_plans(),[])
        self.assertEqual(target.records('reports','p'),[])
        self.assertEqual(target.records('chat_sessions'),[])
        self.assertEqual(target.chat_messages('s'),[])

    def test_two_connections_concurrent_read_write(self):
        a=self.prepare(); b=self.open(); errors=[]; barrier=threading.Barrier(2)
        def write(store,prefix):
            try:
                barrier.wait()
                for i in range(40):
                    store.add_credit('p',f'{prefix}-{i}','u',1,TODAY,'confirmed')
                    store.get_plan('p'); store.load_ledger('p').status(TODAY)
            except BaseException as exc: errors.append(exc)
        threads=[threading.Thread(target=write,args=(a,'a')),threading.Thread(target=write,args=(b,'b'))]
        for t in threads:t.start()
        for t in threads:t.join(15)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(errors,[])
        self.assertEqual(len(a.records('credits','p')),80)
        self.assertEqual(a.load_ledger('p').status(TODAY).remaining_minutes,20)
        self.assertEqual(a._db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(a._db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_every_transactional_api_joins_outer_rollback(self):
        s=self.prepare(); self.prepare(plan='done',modules=1); self.complete(s,'done')
        s.append_message('p','m0','user','old',message_id='old',at=NOW)
        s.create_chat_session('old',session_id='chat')
        source_record=next(r for r in s.pending_records(999) if r.entity=='plans' and r.plan_key=='p')
        calls={
            'create_plan':lambda:s.create_plan('new',window=WINDOW),
            'save_intake':lambda:s.save_intake('p',goal='edited'),
            'add_sources':lambda:s.add_sources('p',[dict(id='newsource',url='https://example.invalid')]),
            'set_source_adopted':lambda:s.set_source_adopted('p','source',False),
            'save_outline':lambda:s.save_outline('p',[dict(id='newstage')],[dict(id='newmodule',stage_id='newstage')]),
            'save_generated_module':lambda:s.save_generated_module('p','m0',[dict(id='point0',summary='updated',minutes=41,chunk_ids=['chunk'])],check_minutes=11,today=TODAY),
            'reorder_modules':lambda:s.reorder_modules('p',['m1','m0'],today=TODAY),
            'delete_module':lambda:s.delete_module('p','m0',today=TODAY),
            'replan_plan':lambda:s.replan_plan('p',TODAY),
            'start_plan':lambda:s.start_plan('p'),
            'archive_plan':lambda:s.archive_plan('p'),
            'restore_plan':lambda:s.restore_plan('p'),
            'delete_plan':lambda:s.delete_plan('p'),
            'append_judgement':lambda:s.append_judgement('p',Judgement('j',1,NOW,'casual','m0',results=(('point0',False),))),
            'append_attempt_closed':lambda:s.append_attempt_closed('p',AttemptClosed('closed',2,'m0','a','module_test')),
            'add_credit':lambda:s.add_credit('p','newcredit','u',1,TODAY),
            'confirm_credits':lambda:s.confirm_credits('p','u',event_id='cf'),
            'revoke_credits':lambda:s.revoke_credits('p','u',event_id='rv'),
            'save_teacher_profile':lambda:s.save_teacher_profile('p',{'profile':'new'}),
            'append_message':lambda:s.append_message('p','m0','user','new',message_id='new',at=NOW),
            'compress_messages':lambda:s.compress_messages('p','m0',['old'],'summary',summary_id='summary',at=NOW),
            'save_report':lambda:s.save_report('done',{'report':True}),
            'append_usage':lambda:s.append_usage('p','u',provider='p',model='m',purpose='test'),
            'create_chat_session':lambda:s.create_chat_session('new',session_id='newchat'),
            'rename_chat_session':lambda:s.rename_chat_session('chat','renamed'),
            'append_chat_message':lambda:s.append_chat_message('chat','user','test',message_id='cm',at=NOW),
            'delete_chat_session':lambda:s.delete_chat_session('chat'),
            'mark_synced':lambda:s.mark_synced(source_record.entity,source_record.entity_id,source_record.local_revision,1),
            'set_sync_cursor':lambda:s.set_sync_cursor('new'),
            'apply_remote':lambda:s.apply_remote([(replace(source_record,payload=dict(source_record.payload,notes='remote')),10)]),
        }
        before=self.snapshot()
        for name,action in calls.items():
            with self.subTest(method=name):
                with self.assertRaisesRegex(RuntimeError,'rollback probe'):
                    with s.transaction(): action(); raise RuntimeError('rollback probe')
                self.assertEqual(self.snapshot(),before)

    def test_failed_projection_rolls_back_remote_journal(self):
        source=self.prepare(); target=self.open(root=self.root/'target'); before=self.snapshot(target)
        original=target._materialize
        def fail(*args): original(*args); raise RuntimeError('projection failed')
        with patch.object(target,'_materialize',side_effect=fail):
            with self.assertRaises(RuntimeError): target.apply_remote([(r,1) for r in source.pending_records(999)])
        self.assertEqual(self.snapshot(target),before)

    def test_checking_and_completion_transitions_are_gated_and_atomic(self):
        s=self.prepare(modules=1); s.start_plan('p'); before=self.snapshot()
        with self.assertRaises(StoreError): s.start_checking('p')
        self.assertEqual(self.snapshot(),before)
        s.append_judgement('p',Judgement('pass',1,NOW,'module_test','m0','a',(('point0',True),)))
        s.append_attempt_closed('p',AttemptClosed('closed',2,'m0','a','module_test'))
        before=self.snapshot()
        with self.assertRaises(RuntimeError):
            with s.transaction(): s.start_checking('p'); raise RuntimeError('rollback')
        self.assertEqual(self.snapshot(),before)
        s.start_checking('p'); before=self.snapshot()
        with self.assertRaises(StoreError): s.complete_plan('p',today=TODAY)
        self.assertEqual(self.snapshot(),before)
        s.append_judgement('p',Judgement('check',3,NOW,'check','m0',results=(('point0',True),),angle='recall'))
        s.add_credit('p','all','u',50,TODAY,'confirmed'); before=self.snapshot()
        with self.assertRaises(RuntimeError):
            with s.transaction(): s.complete_plan('p',today=TODAY); raise RuntimeError('rollback')
        self.assertEqual(self.snapshot(),before)
        s.complete_plan('p',today=TODAY)
        self.assertEqual(s.get_plan('p')['state'],'completed')

    def test_saved_outline_does_not_erase_generated_estimates(self):
        s=self.prepare()
        s.save_outline('p',[],[dict(id='m0',title='改标题',stage_id='stage')])
        self.assertEqual(s.records('modules','p')[0]['learning_minutes'],40)
        before=self.snapshot()
        with self.assertRaises(StoreError):
            s.save_outline('p',[],[dict(id='m0',stage_id='stage',learning_minutes=999)])
        self.assertEqual(self.snapshot(),before)

    def test_remote_mastery_event_ids_conflict_across_event_tables(self):
        s=self.prepare(modules=1)
        s.append_judgement('p',Judgement('event',1,NOW,'casual','m0',results=(('point0',True),)))
        source=next(r for r in s.pending_records(999) if r.entity=='judgements')
        incoming=replace(source,entity='attempt_closed',schema='mentorflow.course.attempt_closed.v1',payload={
            'id':'event','event':{'type':'attempt_closed','event_id':'event','seq':2,'module_id':'m0','attempt_id':'a','kind':'module_test'},'details':{}})
        before=self.snapshot()
        with self.assertRaises(StoreError): s.apply_remote([(incoming,10)])
        self.assertEqual(self.snapshot(),before)

    def test_historical_revocation_survives_sync_and_reopening(self):
        s=self.prepare()
        s.add_credit('p','historical','u',20,TODAY)
        s.replan_plan('p',TODAY+timedelta(days=1))
        frozen=s.load_ledger('p').to_dict()['history']
        s.revoke_credits('p','u',event_id='revoke-history')
        s.add_credit('p','repayment','v',10,TODAY+timedelta(days=1),'confirmed')
        ledger=s.load_ledger('p')
        self.assertEqual(ledger.to_dict()['history'],frozen)
        self.assertEqual(ledger.status(TODAY+timedelta(days=1)).debt_minutes,10)
        target=self.open(root=self.root/'target')
        target.apply_remote([(r,1) for r in reversed(s.pending_records(999))])
        self.assertEqual(target.load_ledger('p').to_dict(),ledger.to_dict())
        reopened=self.open(root=self.root/'target')
        self.assertEqual(reopened.load_ledger('p').to_dict(),ledger.to_dict())

    def test_original_write_failure_is_atomic_and_has_no_outbox_entry(self):
        s=self.store; data=b'new original'; digest=hashlib.sha256(data).hexdigest()
        with patch('refactor.learning.course.store.os.replace',side_effect=OSError('write failed')):
            with self.assertRaises(OSError): s.put_original(data)
        self.assertIsNone(s.get_original(digest))
        self.assertEqual(list((s.root/'files').iterdir()),[])
        self.assertEqual(s.pending_records(100),[])

    def test_pending_new_module_appends_and_blocks_checking(self):
        s=self.prepare(modules=1); s.start_plan('p')
        s.append_judgement('p',Judgement('pass',1,NOW,'module_test','m0','a',(('point0',True),)))
        s.append_attempt_closed('p',AttemptClosed('closed',2,'m0','a','module_test'))
        s.save_outline('p',[],[dict(id='new',stage_id='stage')])
        self.assertEqual([m['id'] for m in s.records('modules','p')],['m0','new'])
        with self.assertRaises(StoreError): s.start_checking('p')
        s.save_generated_module('p','new',[dict(id='newpoint',summary='new',minutes=10,chunk_ids=['chunk'])],check_minutes=2,today=TODAY)
        with self.assertRaises(StoreError): s.start_checking('p')  # New module still untested.

    def test_partial_download_cannot_start_with_missing_points(self):
        source=self.prepare(modules=1)
        source.save_generated_module('p','m0',[
            dict(id='point0',summary='first',minutes=20,chunk_ids=['chunk']),
            dict(id='second',summary='second',minutes=20,chunk_ids=['chunk'])],check_minutes=10,today=TODAY)
        records=source.pending_records(999)
        last=next(r for r in records if r.entity=='points' and r.payload['id']=='second')
        target=self.open(root=self.root/'target')
        target.apply_remote([(r,1) for r in records if r!=last])
        with self.assertRaises(StoreError): target.start_plan('p')
        target.apply_remote([(last,1)]); target.start_plan('p')
        self.assertEqual(target.get_plan('p')['state'],'active')


if __name__ == '__main__': unittest.main()
