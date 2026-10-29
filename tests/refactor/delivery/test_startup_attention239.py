"""No desktop manipulation: fake Win32 calls check text/attention behavior."""
import ctypes
from types import SimpleNamespace

from refactor.desktop.delivery import startup_window as splash


class Call:
    def __init__(self, fn): self.fn = fn
    def __call__(self, *args): return self.fn(*args)


def test_startup239_worker_changes_text_without_activation(monkeypatch, tmp_path):
    calls = []
    user = SimpleNamespace(InvalidateRect=Call(lambda *args: calls.append(args)))
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=user))
    monkeypatch.setattr(splash, '_native', 123)
    monkeypatch.setattr(splash, '_startup_text', '正在启动…')
    splash.set_startup_text('正在更新 MentorFlow…', root=tmp_path)
    assert splash._startup_text == '正在更新 MentorFlow…'
    assert len(calls) == 1 and calls[0][0].value == 123
    assert 'startup_activity' in (tmp_path / 'Data/logs/launcher-startup.jsonl').read_text(encoding='utf8')


def test_startup239_denied_foreground_requests_taskbar_attention(monkeypatch, tmp_path):
    flashes = []
    user = SimpleNamespace(
        ShowWindow=Call(lambda *_: 1),
        GetForegroundWindow=Call(lambda: 77),
        SetForegroundWindow=Call(lambda *_: 0),
        FlashWindowEx=Call(lambda info: flashes.append((info._obj.hwnd, info._obj.flags, info._obj.count)) or 0),
    )
    monkeypatch.setattr(ctypes, 'windll', SimpleNamespace(user32=user))
    monkeypatch.setattr(splash, 'runtime_root', lambda: tmp_path)
    window = SimpleNamespace(showNormal=lambda: None, winId=lambda: 999,
                             raise_=lambda: None, activateWindow=lambda: None)
    splash.foreground(window)
    assert flashes == [(999, 2, 3)]
    log = (tmp_path / 'Data/logs/launcher-startup.jsonl').read_text(encoding='utf8')
    assert 'foreground_attention_requested' in log and '"previous_active": false' in log
    user.SetForegroundWindow = Call(lambda *_: 1)
    splash.foreground(window)
    assert len(flashes) == 1
