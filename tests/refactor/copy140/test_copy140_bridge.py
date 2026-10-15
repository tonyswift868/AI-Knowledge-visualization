"""Write-only clipboard handler remains on the GUI thread and preserves Markdown."""
import pytest
from PySide6.QtGui import QGuiApplication
from refactor.learning.app.bridge import AppBridge, BridgeError
from tests.refactor.app.test_app_window_layer import qapp, make_bridge, pump  # noqa: F401


def test_copy140_bridge_writes_exact_text_and_has_no_read_method(qapp,monkeypatch):
    import json
    writes=[]
    class Clipboard:
        def setText(self,text):writes.append(text)
    monkeypatch.setattr(QGuiApplication,'clipboard',lambda:Clipboard())
    bridge,sent=make_bridge(qapp)
    try:
        assert bridge._handlers['app.copy_text'].ui
        assert not any('clipboard_read' in name or 'paste' in name for name in bridge._handlers)
        text='  **原文**\n\n```x\n1 < 2\n```\n'
        bridge.call(json.dumps(dict(id='copy140',method='app.copy_text',params=dict(text=text))))
        response=pump(qapp,bridge,sent,lambda m:m.get('id')=='copy140')[0]
        assert response['ok'] and writes==[text]
        with pytest.raises(BridgeError,match='复制内容必须是文字'):
            bridge._copy_text({'text':None},None)
        monkeypatch.setattr(QGuiApplication,'clipboard',lambda:None)
        with pytest.raises(BridgeError,match='暂时无法写入剪贴板'):
            bridge._copy_text({'text':text},None)
    finally:bridge.close()
