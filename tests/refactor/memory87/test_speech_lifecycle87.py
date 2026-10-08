"""Real bridge/service lifecycle; only inference and the audio device are fake."""
import importlib.util
from pathlib import Path
import threading

import pytest
from PySide6.QtWidgets import QApplication

_path = Path(__file__).parents[1] / 'teacher_voice_v2/test_teacher_voice_bridge.py'
_spec = importlib.util.spec_from_file_location('memory87_voice_harness', _path)
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)


@pytest.fixture
def h(tmp_path):
    app = QApplication.instance() or QApplication([])
    value = _helpers.Harness(app, tmp_path)
    try:
        yield value
    finally:
        value.close()


def test_memory87_status_does_not_start_optional_engine(h):
    assert h.call('status')['result']['ready']
    _helpers.tick(h.app, .05)
    assert h.voice.service is None and not h.engines


def test_memory87_switch_off_closes_engine_and_next_play_restarts(h):
    h.call('warm_up', owner='page')
    _helpers.spin(h.app, lambda: h.voice._state == 'ready')
    prior = h.voice.service
    assert h.call('stop', owner='page', release=True)['result']['stopped']
    assert h.engines[0].closed and h.voice.service is None
    _helpers.spin(h.app, lambda: not prior._producer.thread.is_alive())
    h.call('speak', owner='page', utterance_id='manual', text='朗读关着也能手动听这一句。')
    _helpers.spin(h.app, lambda: len(h.engines) == 2 and bool(h.sinks))
    assert not h.engines[-1].closed


def test_memory87_switch_off_during_check_ignores_late_result(h):
    h.resources.gate = threading.Event()
    h.call('warm_up', owner='page')
    assert h.resources.entered.wait(.5)
    h.call('stop', owner='page', release=True)
    h.resources.gate.set()
    _helpers.tick(h.app, .08)
    assert h.voice.service is None and not h.engines


def test_memory87_old_page_cannot_release_new_owner(h):
    h.call('speak', owner='new', utterance_id='now', text='新的页面。')
    _helpers.spin(h.app, lambda: bool(h.sinks))
    assert not h.call('stop', owner='old', release=True)['result']['stopped']
    assert not h.engines[0].closed


def test_memory87_idle_retires_ready_engine_and_restarts(h):
    assert h.voice._idle_timer.interval() == 600000
    h.voice._idle_timer.setInterval(50)
    h.call('warm_up', owner='page')
    _helpers.spin(h.app, lambda: h.voice._state == 'ready')
    _helpers.spin(h.app, lambda: h.voice.service is None)
    assert h.engines[0].closed
    h.call('warm_up', owner='page')
    _helpers.spin(h.app, lambda: len(h.engines) == 2)


def test_memory87_idle_never_interrupts_pending_pcm(h):
    h.voice._idle_timer.setInterval(50)
    h.call('speak', owner='page', utterance_id='playing', text='正在朗读不能按空闲回收。')
    _helpers.spin(h.app, lambda: bool(h.sinks) and h.sinks[0].written > 0)
    _helpers.tick(h.app, .1)
    assert h.voice.service is not None and not h.engines[0].closed
    h.sinks[0].played = 1000000
    _helpers.spin(h.app, lambda: h.voice.service is None)
    assert h.engines[0].closed


def test_memory87_dispose_cancels_pending_check_and_closes_owned_engine(h):
    h.call('warm_up', owner='page')
    _helpers.spin(h.app, lambda: h.voice._state == 'ready')
    h.voice.dispose()
    assert h.engines[0].closed and not h.voice._idle_timer.isActive()


def test_memory87_invalid_release_is_rejected(h):
    assert h.call('stop', owner='page', release='yes')['error']['code'] == 'INVALID_PARAMS'
