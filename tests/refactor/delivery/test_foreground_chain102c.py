from refactor.desktop.delivery import startup_window as splash

def test_foreground_root_from_installed_arguments(monkeypatch, tmp_path):
    import sys
    monkeypatch.setattr(sys, 'argv', ['App', '--install-root', str(tmp_path)])
    assert splash.runtime_root() == tmp_path

def test_permission_logs_windows_return_and_foreground(monkeypatch, tmp_path):
    import ctypes
    from types import SimpleNamespace
    class Function:
        def __init__(self, result): self.result = result
        def __call__(self, *args): return self.result
    user=SimpleNamespace(GetForegroundWindow=Function(123),AllowSetForegroundWindow=Function(0))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=user))
    seen=[]
    monkeypatch.setattr(splash, 'log_phase', lambda *a, **k: seen.append((a,k)))
    assert splash.allow_foreground(88,root=tmp_path,stage='app_to_updater') is False
    assert seen[0][1] == dict(stage='app_to_updater',target_pid=88,granted=False,
        foreground_before=123,foreground_after=123)
