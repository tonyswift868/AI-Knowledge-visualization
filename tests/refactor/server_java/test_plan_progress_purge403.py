"""Plan deletion must scrub scoped progress on the Java sync path and locally."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import certifi
import httpx
import pytest
from sqlalchemy import create_engine, text

from contract_support145 import ORG, WORKSPACE, sync145_backends
from test_sync_http_contract145 import BASE, _headers, _wire_json


def _rid(entity, identifier):
    return json.dumps([entity, identifier], ensure_ascii=False, separators=(",", ":"))


def _wire_id(record_id):
    return "refactor.v1:course_record:" + hashlib.sha256(record_id.encode()).hexdigest()


def _payload(entity, plan_key, identifier, content):
    values = {"schema": f"mentorflow.course.{entity}.v1", "plan_key": plan_key,
              "entity": entity, "entity_id": identifier, "payload": content}
    return {"schema": "mentorflow.refactor-sync.v1", "version": 1,
            "record_type": "course_record", "record_id": _rid(entity, identifier),
            "values": values}


def _put(client, token, request, entity, plan_key, identifier, content):
    record_id = _rid(entity, identifier)
    response = client.put(f"{BASE}/entities/learning_run/{_wire_id(record_id)}",
        headers=_headers(token, request), content=_wire_json({"request_id": request,
        "expected_revision": 0, "payload": _payload(entity, plan_key, identifier, content)}))
    assert response.status_code == 200, response.text
    return response


def _purge_migration(connection):
    versions = Path(__file__).resolve().parents[3] / "refactor/server/migrations/versions"
    candidates = sorted(versions.glob("*_purge_deleted_plan_progress.py"))
    assert candidates, "progress purge migration is missing"
    spec = importlib.util.spec_from_file_location("plan_progress_purge403_migration", candidates[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = SimpleNamespace(get_bind=lambda: connection,
        execute=lambda sql: connection.execute(text(sql)),
        get_context=lambda: SimpleNamespace(config=SimpleNamespace(print_stdout=lambda _: None)))
    module.upgrade()


def _live_java_course_transport(clients):
    from refactor.learning.sync_identity import PlanScope
    from refactor.learning.sync_transport import SyncTransport

    issuer, subject = clients["jwks"].issuer, "user-145-a"
    issuer_hash = hashlib.sha256(issuer.encode()).hexdigest()
    subject_hash = hashlib.sha256(subject.encode()).hexdigest()
    account_id = "mfa1_" + hashlib.sha256(
        f"mentorflow.account.v1:{issuer_hash}:{subject_hash}".encode("ascii")).hexdigest()
    scope = PlanScope(account_id, issuer, subject, ORG, WORKSPACE)
    token = clients["jwks"].token(subject=subject)
    state = {"authenticated": True,
             "account": {"account_id": account_id, "subject_id": subject},
             "context": {"organization_id": ORG, "workspace_id": WORKSPACE}}
    session = SimpleNamespace(identity=SimpleNamespace(account_id=account_id, issuer=issuer,
        subject=subject, organization_id=ORG, workspace_id=WORKSPACE),
        server_state=state, access_token=token)

    def forward(request):
        response = clients["java"].request(request.method, request.url.raw_path.decode("ascii"),
            headers=request.headers, content=request.content)
        return httpx.Response(response.status_code, headers=response.headers,
                              content=response.content, request=request)

    return SyncTransport(account_id=account_id, session_provider=lambda: session,
        api_base_url="https://sync.invalid", ca_bundle=Path(certifi.where()),
        http_transport=httpx.MockTransport(forward))


def test_plan_progress403_java_tombstone_rejects_legacy_reupload(sync145_backends):
    clients = sync145_backends
    token = clients["jwks"].token(subject="user-145-a")
    plan = "plan403-" + uuid.uuid4().hex
    other = "other403-" + uuid.uuid4().hex
    marker = "answer403-" + uuid.uuid4().hex
    _put(clients["java"], token, "plan-create", "plans", plan, plan,
         {"id": plan, "goal": "delete me", "archived": False})
    _put(clients["java"], token, "other-create", "plans", other, other,
         {"id": other, "goal": "keep me", "archived": False})
    stale = None
    for entity in ("judgements", "practice_results", "question_answers"):
        progress_id = entity + ":" + plan
        progress_payload = {"id": progress_id, "answer": marker + entity}
        _put(clients["java"], token, "progress-create-" + entity, entity, plan,
             progress_id, progress_payload)
        if entity == "question_answers":
            stale = _payload(entity, plan, progress_id, progress_payload)
    _put(clients["java"], token, "other-progress", "question_answers", other,
         "answer:" + other, {"id": "answer:" + other, "answer": "keep"})
    archive = clients["java"].put(f"{BASE}/entities/learning_run/{_wire_id(_rid('plans', other))}",
        headers=_headers(token, "archive403"), content=_wire_json({"request_id": "archive403",
        "expected_revision": 1, "payload": _payload("plans", other, other,
        {"id": other, "goal": "keep me", "archived": True})}))
    assert archive.status_code == 200, archive.text
    deleted = clients["java"].post(f"{BASE}/entities/learning_run/{_wire_id(_rid('plans', plan))}/delete",
        headers=_headers(token, "delete403"), content=_wire_json({"request_id": "delete403", "expected_revision": 1}))
    assert deleted.status_code == 200, deleted.text

    admin = create_engine(clients["java_admin_url"])
    try:
        with admin.connect() as c:
            c.execute(text("SET row_security=off"))
            progress = c.execute(text("SELECT deleted,payload FROM structured_sync_entities WHERE entity_id=:id"),
                                 {"id": _wire_id(_rid("question_answers", "question_answers:" + plan))}).one()
            assert progress.deleted and progress.payload == {}
            for table in ("structured_sync_entities", "structured_sync_requests"):
                assert c.execute(text(f"SELECT count(*) FROM {table} WHERE row_to_json({table})::text LIKE :m"),
                                 {"m": "%" + marker + "%"}).scalar_one() == 0
            assert c.execute(text("SELECT payload FROM structured_sync_entities WHERE entity_id=:id"),
                             {"id": _wire_id(_rid("question_answers", "answer:" + other))}).scalar_one()["values"]["payload"]["answer"] == "keep"
        # Both a fresh stale-client write and replay of its old request are rejected.
        rejected = clients["java"].put(f"{BASE}/entities/learning_run/{_wire_id(_rid('question_answers','question_answers:'+plan))}",
            headers=_headers(token, "legacy-new403"), content=_wire_json({"request_id": "legacy-new403",
            "expected_revision": 1, "payload": stale}))
        assert rejected.status_code == 410
        assert rejected.json()["code"] == "SYNC_ENTITY_TOMBSTONED"
        replay = clients["java"].put(f"{BASE}/entities/learning_run/{_wire_id(_rid('question_answers','question_answers:'+plan))}",
            headers=_headers(token, "progress-create-question_answers"), content=_wire_json({"request_id": "progress-create-question_answers",
            "expected_revision": 0, "payload": stale}))
        assert replay.status_code == 410, replay.text
        assert replay.json()["code"] == "SYNC_ENTITY_TOMBSTONED"
        with admin.connect() as c:
            c.execute(text("SET row_security=off"))
            row = c.execute(text("SELECT deleted,payload FROM structured_sync_entities WHERE entity_id=:id"),
                            {"id": _wire_id(_rid("question_answers", "question_answers:" + plan))}).one()
            assert row.deleted and row.payload == {}
        missing = "missing-plan403-" + uuid.uuid4().hex
        _put(clients["java"], token, "orphan-progress403", "question_answers", missing,
             "orphan:" + missing, {"id": "orphan:" + missing, "answer": marker + "orphan"})
        tombstone = clients["java"].post(f"{BASE}/entities/learning_run/{_wire_id(_rid('plans', missing))}/tombstone",
            headers=_headers(token, "missing-plan-tombstone403"),
            content=_wire_json({"request_id": "missing-plan-tombstone403", "expected_revision": 0}))
        assert tombstone.status_code == 200, tombstone.text
        with admin.connect() as c:
            c.execute(text("SET row_security=off"))
            orphan = c.execute(text("SELECT deleted,payload FROM structured_sync_entities WHERE entity_id=:id"),
                {"id": _wire_id(_rid("question_answers", "orphan:" + missing))}).one()
            assert orphan.deleted and orphan.payload == {}
    finally:
        admin.dispose()


def test_plan_progress403_local_store_deletes_progress_and_preserves_archive(tmp_path):
    from datetime import date, timedelta
    from refactor.learning.course.ledger import PlanWindow
    from refactor.learning.course.store import CourseStore
    from refactor.learning.course.mastery import Judgement

    store = CourseStore("purge403-local", tmp_path)
    try:
        today = date.today()
        window = PlanWindow(today, today + timedelta(days=2), 60)
        store.create_plan("delete-me", goal="delete-me", final_goal="done", level="beginner",
                          notes="", window=window)
        store.add_sources("delete-me", [dict(id="source403", kind="url", url="https://example.invalid",
            title="source", site="example.invalid", tier="official", credibility=1.0,
            adopted=True, chunks=[dict(chunk_id="chunk403", text="source text", page_start=1,
                                       page_end=1, section=["body"])])])
        store.save_outline("delete-me", [dict(id="stage403", title="stage")],
                           [dict(id="module403", stage_id="stage403", title="module", prerequisites=[])])
        store.save_generated_module("delete-me", "module403", [dict(id="point403", summary="point",
            minutes=20, chunk_ids=["chunk403"])], check_minutes=10, today=today)
        for plan in ("archived", "other"):
            store.create_plan(plan, goal=plan)
        store.archive_plan("archived")
        event = Judgement("answer403", 1, datetime.now(timezone.utc), "casual", "module403",
                          results=(("point403", False),))
        store.append_judgement("delete-me", event, details={"answer": "private403", "reason": "private403"})
        store.delete_plan("delete-me")
        assert store.records("judgements", "delete-me") == []
        assert all(not (r.plan_key == "delete-me" and r.entity in {"judgements", "question_answers", "practice_results"})
                   for r in store.pending_records(1000))
        assert store.get_plan("archived")["archived"] is True
        assert store.get_plan("other")["goal"] == "other"
    finally:
        store.close()


def test_plan_progress403_real_store_sync_delete_sync(sync145_backends, tmp_path):
    from datetime import date, timedelta
    from refactor.learning.course.ledger import PlanWindow
    from refactor.learning.course.mastery import Judgement
    from refactor.learning.course.store import CourseStore
    from refactor.learning.course.sync_course import CourseSync

    clients = sync145_backends
    plan, marker = "e2e403-" + uuid.uuid4().hex, "e2e-private403-" + uuid.uuid4().hex
    store = CourseStore("purge403-e2e", tmp_path)
    transport = _live_java_course_transport(clients)
    admin = create_engine(clients["java_admin_url"])
    try:
        today = date.today()
        store.create_plan(plan, goal="sync and delete", final_goal="done", level="beginner",
                          notes="", window=PlanWindow(today, today + timedelta(days=2), 60))
        store.add_sources(plan, [dict(id="source403", kind="url", url="https://example.invalid",
            title="source", site="example.invalid", tier="official", credibility=1.0,
            adopted=True, chunks=[dict(chunk_id="chunk403", text="source text", page_start=1,
                                       page_end=1, section=["body"])])])
        store.save_outline(plan, [dict(id="stage403", title="stage")],
                           [dict(id="module403", stage_id="stage403", title="module", prerequisites=[])])
        store.save_generated_module(plan, "module403", [dict(id="point403", summary="point",
            minutes=20, chunk_ids=["chunk403"])], check_minutes=10, today=today)
        store.append_judgement(plan, Judgement("answer403", 1, datetime.now(timezone.utc),
            "casual", "module403", results=(("point403", False),)),
            details={"answer": marker, "reason": marker})
        assert CourseSync(store, transport).sync()["ok"] is True
        with admin.connect() as connection:
            connection.execute(text("SET row_security=off"))
            assert connection.execute(text("SELECT count(*) FROM structured_sync_entities WHERE row_to_json(structured_sync_entities)::text LIKE :m"),
                {"m": "%" + marker + "%"}).scalar_one() > 0

        store.delete_plan(plan)
        assert store.records("judgements", plan) == []
        assert not any(row.plan_key == plan and row.entity in {"judgements", "usage", "practice_results", "question_answers"}
                       for row in store.pending_records(1000))
        assert CourseSync(store, transport).sync()["ok"] is True
        with admin.connect() as connection:
            connection.execute(text("SET row_security=off"))
            for table in ("structured_sync_entities", "structured_sync_requests"):
                assert connection.execute(text(f"SELECT count(*) FROM {table} WHERE row_to_json({table})::text LIKE :m"),
                    {"m": "%" + marker + "%"}).scalar_one() == 0
    finally:
        transport.close()
        admin.dispose()
        store.close()


def test_plan_progress403_remote_plan_tombstone_order_does_not_restore_progress(tmp_path):
    from refactor.learning.course.store import CourseStore
    from refactor.learning.course.sync_contract import SyncRecord

    store = CourseStore("purge403-order", tmp_path)
    try:
        store.create_plan("ordered", goal="will be deleted")
        usage = {"id": "usage", "input_tokens": 0, "cache_hit_tokens": 0,
                 "cache_miss_tokens": 0, "output_tokens": 0, "search_count": 0}
        progress = SyncRecord("usage", "7:orderedusage", "ordered",
            "mentorflow.course.usage.v1", usage, False, 1)
        tombstone = SyncRecord("plans", "ordered", "ordered",
            "mentorflow.course.plans.v1", {"id": "ordered"}, True, 2)
        store.apply_remote([(tombstone, 2), (progress, 1)])
        with pytest.raises(ValueError):
            store.get_plan("ordered")
        assert store.records("usage", "ordered") == []

        store.create_plan("reverse", goal="will be deleted")
        reverse_usage = {"id": "usage2", "input_tokens": 0, "cache_hit_tokens": 0,
                         "cache_miss_tokens": 0, "output_tokens": 0, "search_count": 0}
        reverse_progress = SyncRecord("usage", "7:reverseusage2", "reverse",
            "mentorflow.course.usage.v1", reverse_usage, False, 1)
        reverse_tombstone = SyncRecord("plans", "reverse", "reverse",
            "mentorflow.course.plans.v1", {"id": "reverse"}, True, 2)
        store.apply_remote([(reverse_progress, 1), (reverse_tombstone, 2)])
        with pytest.raises(ValueError):
            store.get_plan("reverse")
        assert store.records("usage", "reverse") == []
    finally:
        store.close()


def test_plan_progress403_migration_is_repeatable_and_scoped(sync145_backends):
    clients = sync145_backends
    subject_a = "user-145-a"
    subject_b = "user-145-b"
    token_a = clients["jwks"].token(subject=subject_a)
    token_b = clients["jwks"].token(subject=subject_b)
    plan = "missing-plan403-" + uuid.uuid4().hex
    marker = "migration-answer403-" + uuid.uuid4().hex
    # Build valid historical rows through HTTP. The direct database update
    # below models a legacy server's pre-purge plan tombstone and late progress.
    _put(clients["java"], token_a, "plan-a403", "plans", plan, plan,
         {"id": plan, "goal": "historical plan", "archived": False})
    _put(clients["java"], token_a, "progress-a403", "question_answers", plan,
         "answer:" + plan, {"id": "answer:" + plan, "answer": marker})
    _put(clients["java"], token_b, "plan-b403", "plans", plan, plan,
         {"id": plan, "goal": "other account", "archived": False})
    _put(clients["java"], token_b, "progress-b403", "question_answers", plan,
         "answer:" + plan, {"id": "answer:" + plan, "answer": marker + "-other"})

    engine = create_engine(clients["java_admin_url"])
    migrator = create_engine(clients["java_migration_url"])
    try:
        with engine.begin() as connection:
            connection.execute(text("SET row_security=off"))
            policies_before = connection.execute(text("SELECT tablename,policyname,roles,cmd FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname")).all()
            connection.execute(text("SET LOCAL session_replication_role='replica'"))
            plan_entity = _wire_id(_rid("plans", plan))
            progress_entity = _wire_id(_rid("question_answers", "answer:" + plan))
            connection.execute(text("UPDATE structured_sync_entities SET deleted=true,payload='{}'::json WHERE subject_id=:s AND entity_id=:e"),
                {"s": subject_a, "e": plan_entity})
            progress = connection.execute(text("SELECT subject_id,issuer,organization_id,workspace_id,entity_id FROM structured_sync_entities WHERE subject_id=:s AND entity_id=:e AND payload::text LIKE :m"),
                {"s": subject_a, "e": progress_entity, "m": "%" + marker + "%"}).one()
            assert progress
            live_progress = connection.execute(text("SELECT revision,change_seq FROM structured_sync_entities WHERE subject_id=:s AND entity_id=:e"),
                {"s": subject_a, "e": progress_entity}).one()
            identity = dict(subject=progress.subject_id, issuer=progress.issuer,
                            organization=progress.organization_id, workspace=progress.workspace_id,
                            entity=progress.entity_id)
        with migrator.begin() as connection:
            _purge_migration(connection)
        with engine.begin() as connection:
            once = connection.execute(text("SELECT deleted,payload,revision,change_seq FROM structured_sync_entities WHERE subject_id=:s AND issuer=:i AND organization_id=:o AND workspace_id=:w AND entity_id=:e"),
                {"s": identity["subject"], "i": identity["issuer"], "o": identity["organization"], "w": identity["workspace"], "e": identity["entity"]}).one()
            assert once.deleted and once.payload == {}
            assert once.revision > live_progress.revision and once.change_seq > live_progress.change_seq
        with migrator.begin() as connection:
            _purge_migration(connection)
        with engine.begin() as connection:
            twice = connection.execute(text("SELECT deleted,payload,revision,change_seq FROM structured_sync_entities WHERE subject_id=:s AND issuer=:i AND organization_id=:o AND workspace_id=:w AND entity_id=:e"),
                {"s": identity["subject"], "i": identity["issuer"], "o": identity["organization"], "w": identity["workspace"], "e": identity["entity"]}).one()
            assert twice == once
            policies_after = connection.execute(text("SELECT tablename,policyname,roles,cmd FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname")).all()
            assert policies_after == policies_before
            other_payload = connection.execute(text("SELECT payload FROM structured_sync_entities WHERE subject_id=:s AND entity_id=:e"),
                {"s": subject_b, "e": identity["entity"]}).scalar_one()
            assert other_payload["values"]["payload"]["answer"] == marker + "-other"
    finally:
        engine.dispose()
        migrator.dispose()
