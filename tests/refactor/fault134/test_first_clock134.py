from refactor.learning.connections.providers._accounting import Accounting


def test_first_clock134_29_seconds_counts_and_later_does_not_expire(monkeypatch):
    clock=[100.]
    monkeypatch.setattr('refactor.learning.connections.providers._accounting.time.monotonic',lambda:clock[0])
    a=Accounting(dict(provider_id='deepseek',protocol='anthropic_messages',model='test',enable_search=False))
    a.start();clock[0]=129.
    assert not a.first_expired()
    a.first_event();assert a.receipts[0].first_event_ms==29000
    clock[0]=300.;assert not a.first_expired()
    a.start();clock[0]=330.;assert a.first_expired()
