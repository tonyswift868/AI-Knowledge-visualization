"""Formal bridge wiring for private profile editing."""
from __future__ import annotations

import io
import threading

from refactor.desktop.account_profile import image_preview, encode_crop, data_uri
from refactor.desktop.account_protocol import AccountError
from .bridge import BridgeError


def register(bridge, window):
    def service():
        current = window.services
        if current is None or getattr(current, '_closed', False):
            raise BridgeError('PROFILE_OFFLINE', '当前离线，个人资料只能查看。')
        return current, current.account_profile

    def publish(current, value):
        if window.services is current and not getattr(current, '_closed', False):
            window._personal_profile = value
            bridge.push('account', window.account_info())
        return value

    def run(action, params):
        if action == 'get' and window.services is None:
            return {**getattr(window, '_personal_profile', {}), 'available': True, 'offline': True}
        current, client = service()
        try:
            if action == 'get':
                value = client.refresh()
            elif getattr(window, '_session_offline', False):
                raise AccountError('PROFILE_OFFLINE', '当前离线，个人资料只能查看，联网后才能修改。')
            elif action == 'save':
                value = client.save(params.get('nickname'), params.get('signature'), params.get('version'))
            elif action == 'delete_avatar':
                value = client.delete_avatar(params.get('version'))
            else:
                preview = getattr(current, '_avatar_preview', None)
                if preview is None:
                    raise AccountError('AVATAR_REQUIRED', '请先选择头像。')
                value = client.save_avatar(encode_crop(preview, params.get('crop')), params.get('version'))
                current._avatar_preview = None
            return publish(current, value)
        except AccountError as exc:
            publish(current, client.snapshot())
            raise BridgeError(exc.code, exc.message) from None

    def choose(params, ctx):
        from PySide6.QtWidgets import QFileDialog
        if params:
            raise BridgeError('PARAM_INVALID', '选择头像不接受文件路径参数。')
        current, client = service()
        if getattr(window, '_session_offline', False) or client.offline or not client.available:
            raise BridgeError('PROFILE_OFFLINE', '请联网并读取个人资料后再选择头像。')
        path, _ = QFileDialog.getOpenFileName(window, '选择头像（最多 5 MB）', '', '图片 (*.jpg *.jpeg *.png)')
        if not path:
            return {'cancelled': True}
        try:
            preview = image_preview(path)
        except (AccountError, OSError) as exc:
            raise BridgeError(getattr(exc, 'code', 'AVATAR_READ_FAILED'),
                              getattr(exc, 'message', '头像文件读取失败。')) from None
        current._avatar_preview = preview
        output = io.BytesIO()
        preview.save(output, 'JPEG', quality=85)
        return dict(preview_url=data_uri(output.getvalue()), width=preview.width,
                    height=preview.height, cancelled=False)

    for action in ('get', 'save', 'save_avatar', 'delete_avatar'):
        bridge.register('account.profile_' + action, lambda p, c, action=action: run(action, p))
    bridge.register('account.profile_choose_avatar', choose, ui=True)

    def refresh_background():
        current = window.services
        if current is None or getattr(current, '_closed', False):
            return
        if getattr(current, '_profile_refresh_busy', False):
            return
        current._profile_refresh_busy = True
        def work():
            try:
                publish(current, current.account_profile.refresh())
            except Exception:
                # Optional profile loading never breaks login/learning.
                pass
            finally:
                current._profile_refresh_busy = False
        threading.Thread(target=work, daemon=True, name='account-profile').start()
    return refresh_background
