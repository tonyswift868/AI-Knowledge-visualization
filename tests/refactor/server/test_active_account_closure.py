"""Exercise the live account closure entry without the retired root copy."""
from datetime import timedelta

from test_slim_lifecycle_contract import closure_trace
import test_slim_lifecycle_contract as lifecycle_contract


def test_active_account_closure_prepare_commit_recovery(monkeypatch):
    # The fixture principal used wall-clock ``now`` while NativeSessions read
    # time.time() independently.  A sub-millisecond clock adjustment could make
    # issued_at appear just in the future.  Keep the fixture token comfortably
    # valid; this does not weaken the product's principal validation.
    original = lifecycle_contract.principal
    def stable_principal(root, subject='user-a'):
        value = original(root, subject)
        return value.model_copy(update={"issued_at": value.issued_at - timedelta(seconds=1)})
    monkeypatch.setattr(lifecycle_contract, 'principal', stable_principal)
    assert closure_trace('refactor.server', monkeypatch)
