"""Exercise the live account closure entry without the retired root copy."""
from test_slim_lifecycle_contract import closure_trace


def test_active_account_closure_prepare_commit_recovery(monkeypatch):
    assert closure_trace('refactor.server', monkeypatch)
