"""Real QWidget geometry and real app.window routing, isolated account preferences."""
import json

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QWidget

from refactor.learning.app.window import _TeacherDrawer, LearningWindow
from refactor.learning.app.bridge import AppBridge
from refactor.learning.app import handlers
from refactor.learning.preferences.storage import AccountJSON


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def make(app, tmp_path, x=100, screen_width=2200):
    widget = QWidget()
    widget.setMinimumSize(1100, 720)
    widget.setGeometry(x, 70, 1200, 800)
    widget.show(); app.processEvents()
    events = []
    class FixtureDrawer(_TeacherDrawer):
        def available(self):
            return QRect(0, 0, screen_width, 1200)
    drawer = FixtureDrawer(widget, AccountJSON('teacher78', tmp_path, 'teacher-drawer.json'), events.append)
    return widget, drawer, events


def drain(app):
    for _ in range(4):
        app.processEvents()


@pytest.mark.parametrize('x', [100, 950])
def test_teacher78_regular_extension_shift_and_close(app, tmp_path, x):
    widget, drawer, _ = make(app, tmp_path, x)
    try:
        before = QRect(widget.geometry())
        result = drawer.action('teacher_open', {'width':320})
        assert result['mode'] == 'external'
        assert widget.width() == before.width() + 288
        assert widget.width() - result['width'] + 32 == before.width()
        assert widget.frameGeometry().right() <= 2199
        assert widget.geometry().x() == before.x() if x == 100 else widget.geometry().x() < before.x()
        drawer.action('teacher_close', {})
        assert widget.geometry() == before
        drawer.action('teacher_close', {})
        assert widget.geometry() == before  # Idempotent close.
    finally:
        widget.close()


def test_teacher78_drag_width_manual_resize_and_restore(app, tmp_path):
    widget, drawer, _ = make(app, tmp_path)
    try:
        before = QRect(widget.geometry())
        drawer.action('teacher_open', {'width':320})
        drawer.action('teacher_open', {'width':420})
        assert widget.width() == before.width() + 388
        drawer.action('teacher_open', {'width':420})
        assert widget.width() == before.width() + 388  # No accumulating extension.
        widget.resize(widget.width()+130, widget.height()+10)
        drawer.action('teacher_close', {})
        assert widget.width() == before.width()+130
        assert widget.height() == before.height()+10
        assert widget.geometry().topLeft() == before.topLeft()
    finally:
        widget.close()


def test_teacher78_screen_too_small_uses_internal_layout(app, tmp_path):
    widget, drawer, _ = make(app, tmp_path, screen_width=1400)
    try:
        before = QRect(widget.geometry())
        state = drawer.action('teacher_open', {'width':320})
        assert state['mode'] == 'internal' and state['reason'] == 'screen_too_small'
        assert widget.geometry() == before
        drawer.action('teacher_close', {})
        assert widget.geometry() == before
    finally:
        widget.close()


@pytest.mark.parametrize('mode', ['maximized','fullscreen'])
def test_teacher78_special_state_stays_internal_and_restores(app, tmp_path, mode):
    widget, drawer, _ = make(app, tmp_path)
    try:
        before = QRect(widget.geometry())
        (widget.showMaximized if mode=='maximized' else widget.showFullScreen)(); drain(app)
        special = QRect(widget.geometry())
        state = drawer.action('teacher_open', {'width':320})
        assert state['mode'] == 'internal' and state['reason'] == 'window_state'
        assert widget.geometry() == special
        drawer.action('teacher_close', {})
        assert widget.geometry() == special
        widget.showNormal(); drain(app)
        assert widget.geometry() == before
    finally:
        widget.close()


def test_teacher78_fullscreen_while_open_does_not_double_expand(app, tmp_path):
    widget, drawer, _ = make(app, tmp_path)
    try:
        before = QRect(widget.geometry())
        drawer.action('teacher_open', {'width':320})
        widget.showFullScreen(); drain(app)
        assert drawer.mode == 'internal'
        widget.showNormal(); drain(app)
        assert drawer.mode == 'external' and widget.width() == before.width()+288
        drawer.action('teacher_close', {})
        assert widget.geometry() == before
    finally:
        widget.close()


def test_teacher78_recreated_window_restores_open_width_and_view(app, tmp_path):
    widget, drawer, _ = make(app, tmp_path)
    before = QRect(widget.geometry())
    view = dict(scale=1.7,panX=.1,panY=-.2)
    drawer.action('teacher_open', dict(width=380,view=view))
    drawer.save()
    widget.close()
    second, restored, _ = make(app, tmp_path)
    try:
        state = restored.action('teacher_open', dict(restore=True,open=False,width=320))
        assert state['open'] and state['width']==380 and state['view']==view
        assert second.width()==before.width()+348
        restored.action('teacher_close', {'preserve':True})
        assert restored.preferences.read({})['open'] is True
        restored.save()  # Window disposal after leaving classroom must not forget the choice.
        assert restored.preferences.read({})['open'] is True
        assert second.geometry()==before
    finally:
        second.close()


def test_teacher78_real_bridge_teacher_actions_and_existing_fullscreen(app, tmp_path):
    widget, drawer, _ = make(app, tmp_path)
    bridge = AppBridge()
    widget._teacher_drawer = drawer
    widget.bridge = bridge
    widget.services = None
    widget.session_notice = None
    widget.window_action = lambda action, **params: LearningWindow.window_action(widget, action, **params)
    handlers.register(bridge, widget)
    sent = []
    bridge.event.connect(lambda text: sent.append(json.loads(text)))
    try:
        for i,params in enumerate([{'action':'teacher_open','width':320},{'action':'teacher_close'},
                                   {'action':'teacher_open','width':-1},{'action':'toggle_fullscreen'},
                                   {'action':'exit_fullscreen'}]):
            bridge.call(json.dumps(dict(id=f'r{i}',method='app.window',params=params)))
            drain(app)
        responses = {m['id']:m for m in sent if m['type']=='response'}
        assert responses['r0']['result']['teacher']['mode']=='external'
        assert not responses['r1']['result']['teacher']['open']
        assert responses['r2']['error']['code']=='PARAM_INVALID'
        assert responses['r3']['result']['fullscreen'] is True
        assert responses['r4']['result']['fullscreen'] is False
    finally:
        bridge.close(); widget.close()
