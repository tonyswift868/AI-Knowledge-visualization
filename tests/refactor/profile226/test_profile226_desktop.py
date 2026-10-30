import io
import json
from types import SimpleNamespace as NS

import certifi
import httpx
import pytest
from PIL import Image

from refactor.desktop.account_profile import (
    AccountProfileClient, MAX_ORIGINAL, cached_profile, image_preview, encode_crop,
    data_uri, _storage,
)
from refactor.desktop.account_protocol import AccountError

A = 'mfa1_' + 'a' * 64
B = 'mfa1_' + 'b' * 64


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (64, 64), '#d65a86').save(out, 'JPEG')
    return out.getvalue()


def client(tmp_path, handler, account=A):
    active = [NS(identity=NS(account_id=account), access_token=account)]
    value = AccountProfileClient(account_id=account, data_root=tmp_path,
        session_provider=lambda: active[0], api_base_url='https://profile.invalid',
        ca_bundle=certifi.where(), http_transport=httpx.MockTransport(handler))
    return value, active


def test_profile226_client_caches_per_account_and_uses_fresh_owner(tmp_path):
    calls = []
    def server(req):
        calls.append(req)
        owner = req.headers['Authorization'].removeprefix('Bearer ')
        assert 'x-mentorflow-organization-id' not in req.headers
        if req.url.path.endswith('/avatar'):
            return httpx.Response(304 if req.headers.get('if-none-match') == '"a"' else 200,
                                  content=b'' if req.headers.get('if-none-match') else jpeg(), headers={'ETag': '"a"'})
        return httpx.Response(200, json=dict(nickname=owner[-1], signature='<img onerror=bad>', version=1,
            avatar=dict(etag='"a"', width=64, height=64)))
    a, active = client(tmp_path, server)
    assert a.refresh()['nickname'] == 'a'
    assert a.refresh()['avatar_url'].startswith('data:image/jpeg;base64,')
    b, _ = client(tmp_path, server, B)
    assert b.refresh()['nickname'] == 'b'
    assert cached_profile(A, tmp_path)['nickname'] == 'a'
    active[0] = NS(identity=NS(account_id=B), access_token=B)
    assert a.refresh()['offline']
    assert cached_profile(A, tmp_path)['nickname'] == 'a'
    assert len(calls) == 6
    # A second device has no local cache: the same authenticated account still
    # receives its server profile and creates an independent local copy.
    other_device = tmp_path / 'another-device'
    assert cached_profile(A, other_device)['nickname'] == ''
    fresh, _ = client(other_device, server)
    profile = fresh.refresh()
    assert profile['nickname'] == 'a'
    assert profile['avatar_url'] == cached_profile(A, tmp_path)['avatar_url']
    assert cached_profile(A, other_device)['signature'] == '<img onerror=bad>'


def test_profile226_old_server_and_offline_are_not_login_failures(tmp_path):
    a, active = client(tmp_path, lambda _: httpx.Response(404))
    assert a.refresh() == dict(nickname='', signature='', version=1, avatar=None, avatar_url='', available=False, offline=False)
    a, active = client(tmp_path, lambda _: httpx.Response(503))
    assert a.refresh()['offline']
    with pytest.raises(AccountError) as err:
        a.save('新昵称', '', 1)
    assert err.value.code == 'PROFILE_FAILED'
    assert a.offline
    active[0] = None
    assert a.refresh()['offline']
    with pytest.raises(AccountError, match='只能查看'):
        a.save('新昵称', '', 1)
    assert cached_profile(A, tmp_path)['nickname'] == ''


def test_profile226_closed_client_cannot_publish_reply(tmp_path):
    def server(req):
        a.close()
        return httpx.Response(200, json=dict(nickname='迟到', signature='', version=1, avatar=None))
    a, _ = client(tmp_path, server)
    assert a.refresh()['offline']
    assert cached_profile(A, tmp_path)['nickname'] == ''


def test_profile226_mutation_cas_and_unicode(tmp_path):
    def server(req):
        assert req.headers['if-match'] == '"profile-1"'
        value = json.loads(req.content)
        return httpx.Response(200, json={**value, 'version': 2, 'avatar': None})
    a, _ = client(tmp_path, server)
    assert a.save('😀' * 30, '字' * 60, 1)['version'] == 2
    assert cached_profile(A, tmp_path)['nickname'] == '😀' * 30
    with pytest.raises(AccountError):
        a.save('😀' * 31, '', 2)


def test_profile226_older_refresh_cannot_undo_saved_revision(tmp_path):
    def server(req):
        if req.method == 'GET':
            a.save('已保存的新昵称', '', 1)
            return httpx.Response(200, json=dict(nickname='旧昵称', signature='', version=1, avatar=None))
        return httpx.Response(200, json=dict(nickname='已保存的新昵称', signature='', version=2, avatar=None))
    a, _ = client(tmp_path, server)
    assert a.refresh()['version'] == 2
    assert cached_profile(A, tmp_path)['nickname'] == '已保存的新昵称'


def test_profile226_crop_orientation_metadata_and_limits(tmp_path):
    path = tmp_path / 'original.jpg'
    original = Image.new('RGB', (80, 40), 'red')
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = 'private location annotation'
    original.save(path, exif=exif)
    preview = image_preview(path)
    assert preview.size == (40, 80)
    raw = encode_crop(preview, dict(x=0, y=10, size=40))
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (40, 40)
        assert not image.getexif()
        assert 'private location' not in str(image.info)
    with pytest.raises(AccountError):
        encode_crop(preview, dict(x=0, y=0, size=99))
    with path.open('wb') as stream:
        stream.truncate(MAX_ORIGINAL + 1)
    with pytest.raises(AccountError, match='5 MB'):
        image_preview(path)
    path.write_bytes(b'not an image')
    with pytest.raises(AccountError):
        image_preview(path)


def test_profile226_formal_handlers_and_bridge_wire(tmp_path):
    from PySide6.QtWidgets import QApplication
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.app import handlers
    from refactor.learning.app.window import LearningWindow
    import time
    app = QApplication.instance() or QApplication([])
    a, _ = client(tmp_path, lambda _: httpx.Response(200, json=dict(nickname='小明', signature='', version=1, avatar=None)))
    current = NS(account_profile=a, _closed=False)
    window = NS(services=current, _personal_profile={}, _display_name='login_name', _account_id=A,
                _role='LEARNER', _api_label='fixture', _settings_bound=True, _session_offline=False)
    window.account_info = lambda: LearningWindow.account_info(window)
    bridge = AppBridge()
    handlers.register(bridge, window)
    events=[]
    bridge.event.connect(lambda raw: events.append(json.loads(raw)))
    bridge.call(json.dumps(dict(id='profile226', method='account.profile_get', params={})))
    deadline = time.monotonic() + 5
    while not any(e.get('id') == 'profile226' for e in events) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    result = next(e for e in events if e.get('id') == 'profile226')
    assert result['ok'], result
    assert result['result']['nickname'] == '小明'
    account = next(e['data'] for e in events if e.get('name') == 'account')
    assert account['name'] == 'login_name' and account['nickname'] == '小明'
    assert account['role_label'] == '学习者'
    bridge.close()


def test_profile226_unbound_window_still_shows_offline_cache():
    from PySide6.QtWidgets import QApplication
    from refactor.learning.app.bridge import AppBridge, CallContext
    from refactor.learning.app import handlers
    app = QApplication.instance() or QApplication([])
    window = NS(services=None, _personal_profile=dict(nickname='缓存昵称', signature='缓存签名',
        version=3, avatar=None, avatar_url=''))
    bridge = AppBridge()
    handlers.register(bridge, window)
    result = bridge._handlers['account.profile_get'].function({}, None)
    assert result['nickname'] == '缓存昵称' and result['offline']
    bridge.close()


def test_profile226_login_popup_cache_remove_and_screenshots(tmp_path):
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPainter
    from refactor.desktop.account_shell_main import AccountShell
    from refactor.desktop.login_shell import LoginGateWindow, LoginGateState
    app = QApplication.instance() or QApplication([])
    from tests.refactor.simulation.engine import ensure_offscreen_fonts
    ensure_offscreen_fonts()
    rows = [dict(account_id=A, username='learner-a', display_name='用户A'),
            dict(account_id=B, username='learner-b', display_name='用户B')]
    for account, name in ((A, '小明'), (B, '小红')):
        _storage(account, tmp_path).write(dict(account_id=account, nickname=name, signature='',
            version=1, avatar=dict(etag='"x"', width=64, height=64), avatar_url=data_uri(jpeg())))
    gate = LoginGateWindow()
    gate.set_state(LoginGateState.LOGIN_READY)
    shell = object.__new__(AccountShell)
    shell.runtime = NS(data_root=tmp_path)
    shell.gate = gate
    shell.session = NS(registry=NS(snapshot=lambda: dict(accounts=rows, current_account_id=A)),
        remove_local=lambda account: rows.__setitem__(slice(None), [row for row in rows if row['account_id'] != account]))
    shell._invalidate_login_jobs = lambda: None
    shell._cancel_login = lambda: None
    shell._refresh_local_accounts()
    assert gate.account_selector.itemText(0) == '小明 · learner-a'
    assert not gate.account_selector.itemIcon(0).isNull()
    assert gate.username_field.text() == 'learner-a'
    shots = tmp_path / 'screenshots'
    shots.mkdir(parents=True, exist_ok=True)
    for theme in ('sakura', 'aurora'):
        gate.set_loading_appearance(theme)
        gate.resize(640, 780)
        gate.show()
        app.processEvents()
        gate.account_selector.showPopup()
        app.processEvents()
        pix = gate.grab()
        popup = gate.account_selector.view().window()
        painter = QPainter(pix)
        point = gate.mapFromGlobal(popup.pos())
        painter.drawPixmap(point, popup.grab())
        painter.end()
        assert pix.save(str(shots / f'login-profile226-{theme}.png'))
        gate.account_selector.hidePopup()
    shell._remove_local_account(A)
    assert cached_profile(A, tmp_path)['nickname'] == ''
    assert cached_profile(B, tmp_path)['nickname'] == '小红'
    assert len(rows) == 1
    gate.hide()
    gate.deleteLater()
    app.processEvents()


def test_profile226_full_bridge_edit_crop_save_remove(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QFileDialog
    from refactor.learning.app.bridge import AppBridge
    from refactor.learning.app import handlers
    import time
    app = QApplication.instance() or QApplication([])
    metadata = dict(nickname='', signature='', version=1, avatar=None)
    uploaded = []
    def server(req):
        if req.method in ('PATCH', 'PUT', 'DELETE'):
            assert req.headers['if-match'] == f'"profile-{metadata["version"]}"'
            metadata['version'] += 1
        if req.method == 'PATCH':
            metadata.update(json.loads(req.content))
        elif req.method == 'PUT':
            uploaded.append(req.content)
            metadata['avatar'] = dict(etag='"new-avatar"', width=40, height=40)
        elif req.method == 'DELETE':
            metadata['avatar'] = None
        elif req.url.path.endswith('/avatar'):
            return httpx.Response(200, content=uploaded[-1], headers={'ETag': '"new-avatar"'})
        return httpx.Response(200, json=metadata)
    a, _ = client(tmp_path, server)
    current = NS(account_profile=a, _closed=False)
    window = NS(services=current, _session_offline=False, _personal_profile={})
    window.account_info = lambda: dict(name='unchanged-login', **window._personal_profile)
    bridge = AppBridge()
    handlers.register(bridge, window)
    events=[]
    bridge.event.connect(lambda raw: events.append(json.loads(raw)))
    def invoke(action, params=None):
        identifier = str(len(events))
        bridge.call(json.dumps(dict(id=identifier, method='account.profile_' + action, params=params or {})))
        deadline=time.monotonic()+5
        while not any(e.get('id') == identifier for e in events) and time.monotonic()<deadline:
            app.processEvents(); time.sleep(.005)
        response=next(e for e in events if e.get('id') == identifier)
        assert response['ok'], response
        return response['result']
    assert invoke('get')['version'] == 1
    assert invoke('save', dict(nickname='昵称', signature='<script>literal</script>', version=1))['version'] == 2
    original=tmp_path/'original.png'
    Image.new('RGB',(40,80),'blue').save(original)
    monkeypatch.setattr(QFileDialog,'getOpenFileName', lambda *args: (str(original),''))
    # The chooser runs on UI thread and a real cropped image passes through the formal handler.
    assert invoke('choose_avatar')['height'] == 80
    assert invoke('save_avatar', dict(crop=dict(x=0,y=10,size=40),version=2))['version'] == 3
    assert cached_profile(A,tmp_path)['avatar_url'] == data_uri(uploaded[0])
    assert invoke('delete_avatar',dict(version=3))['avatar_url'] == ''
    assert cached_profile(A,tmp_path)['nickname'] == '昵称'
    bridge.close()
