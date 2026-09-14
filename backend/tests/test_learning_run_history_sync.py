from types import SimpleNamespace
from datetime import datetime, timezone
import pytest
from app.enterprise_tenancy.learning_run_history_sync import *

S=TrustedSyncScope("iss","sub","org","ws")
def run(): return SimpleNamespace(id="run_1",workspace_id="ws",task_id="t",lesson_id="l",phase="explanation",status="active",revision=2,state={},created_at=datetime.now(timezone.utc),updated_at=datetime.now(timezone.utc))
def msg(i): return SimpleNamespace(id=i,run_id="run_1",role="user",phase="explanation",content=i,grounding={},created_at=datetime(2025,1,1,tzinfo=timezone.utc),idempotency_key=i)
def test_projection_is_scoped_and_ordered():
    assert project_run(run(),S)["entity_id"]=="run_1"
    assert [x["message_id"] for x in project_history([msg("b"),msg("a")],S,"run_1")["entries"]]==["a","b"]
def test_wrong_workspace_and_unsupported_fail_closed():
    bad=SimpleNamespace(**{**run().__dict__,"workspace_id":"other"})
    with pytest.raises(ValueError): project_run(bad,S)
    with pytest.raises(ValueError): reject_unsupported("free_chat")
def test_empty_history_is_valid():
    assert project_history([],S,"run_1")["history_empty"] is True
