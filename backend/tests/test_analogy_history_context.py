"""Exercise the real model-message assembly; intercept before any provider call."""
import asyncio
import json
from types import SimpleNamespace

import pytest


def test_scoped_desktop_history_reaches_model_context_without_other_account(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from app import main as route
    from desktop.tests.test_analogy_history_isolation import (
        HistoryWindow, activate, write_history, assembled_request,
    )
    from desktop.runtime import clear_enterprise_account_session
    application = QApplication.instance() or QApplication([])
    captured = []

    class ContextCaptured(BaseException):
        """Stop before model invocation or response validation; never fake success."""

    async def intercept(_session, messages, **kwargs):
        captured.append(json.loads(messages[-1]['content']))
        raise ContextCaptured()

    monkeypatch.setattr(route, 'require_provider_session', lambda *_a, **_k: SimpleNamespace(provider='openai-compatible', verified=True))
    monkeypatch.setattr(route, 'call_bound_provider_json', intercept)
    ini = tmp_path/'context.ini'
    try:
        for subject in ('a', 'b', 'a'):
            window = HistoryWindow(ini, activate(subject))
            marker = subject.upper() + '_ONLY_RECENT'
            other = ('B' if subject == 'a' else 'A') + '_ONLY_RECENT'
            write_history(window, marker)
            body = assembled_request(window)[0]
            with pytest.raises(ContextCaptured):
                asyncio.run(route.create_life_analogy(None, body))
            context = captured[-1]
            assert context['recent_history']
            assert marker in json.dumps(context['recent_history'])
            assert marker in json.dumps(context['forbidden_concepts'])
            assert other not in json.dumps(context)
            window._seal_analogy_history()
        assert len(captured) == 3
    finally:
        clear_enterprise_account_session()
