"""Authenticated product runtime must not be masked by the bootstrap DTO."""
from types import SimpleNamespace

import pytest

from desktop.main_window import MainWindow


@pytest.mark.parametrize('surface', ['tasks', 'overview'])
def test_product_plan_views_refresh_account_runtime_not_stale_bootstrap(monkeypatch, surface):
    monkeypatch.setattr('desktop.main_window.QTimer.singleShot', lambda *args: None)
    calls = []
    status = SimpleNamespace(setText=lambda text: None, setVisible=lambda value: None)
    workspace_items = lambda **kw: []
    view = SimpleNamespace(
        bootstrap_snapshot=SimpleNamespace(plans=(), progress=()),
        _rehydration_session=object(),  # Supplied only by verified account service startup.
        _workspace_items=workspace_items,
        _workspace_executor=SimpleNamespace(submit=lambda fn, **kw: calls.append((fn, kw))),
        _workspace_cache={}, _workspace_refresh_token=0, _overview_workspace_token=0,
        include_archived_plans=SimpleNamespace(isChecked=lambda: False),
        task_loading_status=status, overview_status=status, overview_status_icon=status,
        overview_task_list=object(),
        _show_account_plan_metadata=lambda *args: pytest.fail('stale bootstrap masked product state'),
    )
    getattr(MainWindow, '_refresh_' + surface)(view)
    assert calls == [(workspace_items, {'include_archived': False})]
