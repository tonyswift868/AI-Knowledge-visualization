"""Bridge-level archive send cancellation and concurrent message conflict checks."""
import json

from refactor.learning.chat.contracts import utc_now

from tests.refactor.chat175.test_chat175_backend import _call, _setup
from tests.refactor.app.test_app_free_chat import pump
from tests.refactor.app.test_app_window_layer import qapp, tmp_root  # noqa: F401


def test_cancelled_send_from_archived_chat_keeps_auto_restore_but_saves_no_turn(qapp, tmp_root):
    services, bridge, sent = _setup(qapp, tmp_root)
    try:
        sid = _call(bridge, sent, qapp, 'new', 'chat.create')['result']['session']['id']
        _call(bridge, sent, qapp, 'archive', 'chat.archive', {'session_id': sid, 'archived': True})
        services.gateway.block = True
        bridge.call(json.dumps({'id': 'pending-send', 'method': 'chat.send',
                                'params': {'session_id': sid, 'text': '继续聊'}}))
        assert services.gateway.entered.wait(3)
        assert services.store.get_chat_session(sid)['archived'] is False
        bridge.call(json.dumps({'id': 'cancel-send', 'method': 'cancel', 'params': {'target': 'pending-send'}}))
        stopped = pump(qapp, bridge, sent,
                       lambda msg: msg.get('id') == 'pending-send' and msg['type'] == 'response')[0]
        assert stopped['error']['code'] == 'CANCELLED'
        current = _call(bridge, sent, qapp, 'get-after-cancel', 'chat.get', {'session_id': sid})['result']['session']
        assert current['archived'] is False
        assert current['messages'] == []
    finally:
        services.gateway.release.set()
        bridge.close()
        services.close()


def test_remote_message_change_during_generation_still_conflicts(qapp, tmp_root):
    services, bridge, sent = _setup(qapp, tmp_root)
    try:
        sid = _call(bridge, sent, qapp, 'new', 'chat.create')['result']['session']['id']
        services.gateway.block = True
        bridge.call(json.dumps({'id': 'pending-send', 'method': 'chat.send',
                                'params': {'session_id': sid, 'text': '正在生成的问题'}}))
        assert services.gateway.entered.wait(3)
        services.store.append_chat_message(sid, 'assistant', '另一设备刚写入的回复',
                                           message_id='remote-message', at=utc_now().isoformat())
        services.gateway.release.set()
        result = pump(qapp, bridge, sent,
                      lambda msg: msg.get('id') == 'pending-send' and msg['type'] == 'response')[0]
        assert result['error']['code'] == 'CHAT_CONFLICT'
        saved = _call(bridge, sent, qapp, 'get-after-conflict', 'chat.get', {'session_id': sid})['result']['session']
        assert [message['content'] for message in saved['messages']] == ['另一设备刚写入的回复']
    finally:
        services.gateway.release.set()
        bridge.close()
        services.close()
