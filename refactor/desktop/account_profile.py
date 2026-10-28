"""Private account profile transport/cache; login identity remains untouched."""
from __future__ import annotations

import base64
import io
import json
import ssl
import threading
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from refactor.learning.preferences.storage import AccountJSON
from .account_protocol import AccountError

PROFILE_PATH = '/api/enterprise/account/profile'
CACHE_FILE = 'personal-profile.json'
MAX_ORIGINAL = 5_000_000
MAX_UPLOAD = 262_144
MAX_PIXELS = 32_000_000


def _storage(account_id, data_root):
    # One atomic record keeps the server image and its revision consistent.
    return AccountJSON(account_id, data_root, CACHE_FILE, max_bytes=400_000)


def cached_profile(account_id, data_root):
    empty = dict(nickname='', signature='', version=1, avatar=None, avatar_url='')
    try:
        value = _storage(account_id, data_root).read({})
        if value.get('account_id') != account_id:
            return empty
        _validate(value)
        uri = value.get('avatar_url', '')
        if not isinstance(uri, str) or (uri and not uri.startswith('data:image/jpeg;base64,')):
            return empty
        return {key: value.get(key, default) for key, default in empty.items()}
    except (OSError, ValueError, AccountError):
        return empty


def remove_cached_profile(account_id, data_root):
    store = _storage(account_id, data_root)
    store._check()
    store.path.unlink(missing_ok=True)


def _validate(value):
    if (not isinstance(value, dict) or not isinstance(value.get('nickname'), str)
            or len(value['nickname']) > 30 or not isinstance(value.get('signature'), str)
            or len(value['signature']) > 60 or type(value.get('version')) is not int
            or value['version'] < 1):
        raise AccountError('PROFILE_INVALID', '个人资料格式无效，请重新读取。')
    avatar = value.get('avatar')
    if avatar is not None and (not isinstance(avatar, dict)
            or not isinstance(avatar.get('etag'), str) or len(avatar['etag']) > 200):
        raise AccountError('PROFILE_INVALID', '头像版本无效，请重新读取。')


def image_preview(path):
    """No original metadata escapes this function. Crop uses preview pixels."""
    path = Path(path)
    if path.stat().st_size > MAX_ORIGINAL:
        raise AccountError('AVATAR_TOO_LARGE', '头像原图不能超过 5 MB。')
    with path.open('rb') as stream:
        raw = stream.read(MAX_ORIGINAL + 1)
    if len(raw) > MAX_ORIGINAL:
        raise AccountError('AVATAR_TOO_LARGE', '头像原图不能超过 5 MB。')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('JPEG', 'PNG') or getattr(source, 'n_frames', 1) != 1:
                raise ValueError()
            if source.width * source.height > MAX_PIXELS or max(source.size) > 16384:
                raise ValueError()
            oriented = ImageOps.exif_transpose(source)
            oriented.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            pixels = Image.new('RGB', oriented.size, 'white')
            rgba = oriented.convert('RGBA')
            pixels.paste(rgba, mask=rgba.getchannel('A'))
            return pixels
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise AccountError('AVATAR_INVALID', '请选择有效的 JPEG 或 PNG 静态图片（最多 3200 万像素）。') from None


def encode_crop(preview, crop):
    if not isinstance(crop, dict) or set(crop) != {'x', 'y', 'size'}:
        raise AccountError('AVATAR_CROP_INVALID', '请选择正方形裁剪范围。')
    x, y, size = (crop[k] for k in ('x', 'y', 'size'))
    if any(type(v) not in (int, float) for v in (x, y, size)):
        raise AccountError('AVATAR_CROP_INVALID', '裁剪范围无效。')
    import math
    if (not all(math.isfinite(v) for v in (x, y, size)) or size < 1 or x < 0 or y < 0
            or x + size > preview.width + .001 or y + size > preview.height + .001):
        raise AccountError('AVATAR_CROP_INVALID', '裁剪范围超出图片。')
    side = min(512, max(1, round(size)))
    cropped = preview.crop((round(x), round(y), round(x + size), round(y + size)))
    cropped = cropped.resize((side, side), Image.Resampling.LANCZOS)
    pixels = Image.new('RGB', cropped.size)
    pixels.paste(cropped)
    for quality in (85, 75, 65):
        out = io.BytesIO()
        pixels.save(out, 'JPEG', quality=quality, optimize=True)
        data = out.getvalue()
        if len(data) <= MAX_UPLOAD:
            return data
    raise AccountError('AVATAR_TOO_LARGE', '裁剪后的头像仍过大，请换一张图片。')


def data_uri(data):
    return 'data:image/jpeg;base64,' + base64.b64encode(data).decode('ascii')


class AccountProfileClient:
    def __init__(self, *, account_id, data_root, session_provider, api_base_url, ca_bundle,
                 http_transport=None):
        url = urlsplit(api_base_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('invalid account API address')
        self.account_id, self.data_root = account_id, data_root
        self._provider, self._base = session_provider, api_base_url.rstrip('/')
        self._ssl = ssl.create_default_context(cafile=str(ca_bundle))
        self._transport = http_transport
        self._lock = threading.RLock()
        self._closed = False
        self._value = cached_profile(account_id, data_root)
        self.available, self.offline = True, True

    def close(self):
        with self._lock:
            self._closed = True

    def _session(self):
        current = self._provider()
        if (self._closed or current is None or current.identity.account_id != self.account_id
                or not current.access_token):
            raise AccountError('PROFILE_OFFLINE', '当前离线，个人资料只能查看，联网后才能修改。')
        return current

    def snapshot(self):
        with self._lock:
            return {**self._value, 'available': self.available, 'offline': self.offline}

    def _request(self, method, suffix='', **kwargs):
        current = self._session()
        headers = {'Authorization': 'Bearer ' + current.access_token, **kwargs.pop('headers', {})}
        try:
            with httpx.Client(verify=self._ssl, timeout=15, follow_redirects=False, transport=self._transport) as client:
                with client.stream(method, self._base + PROFILE_PATH + suffix, headers=headers, **kwargs) as response:
                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        raw.extend(chunk)
                        if len(raw) > MAX_UPLOAD:
                            raise AccountError('PROFILE_INVALID', '个人资料响应过大。')
                    self._session()  # switched/closed account must not publish a late reply
                    return response.status_code, dict(response.headers), bytes(raw)
        except (httpx.HTTPError, OSError):
            self.offline = True
            raise AccountError('PROFILE_OFFLINE', '网络不可用，个人资料未保存；联网后请重试。') from None

    def _status(self, status):
        if status in (404, 405):
            self.available = False
            raise AccountError('PROFILE_UNSUPPORTED', '当前服务器尚未提供个人资料功能。')
        if status == 412:
            raise AccountError('PROFILE_CONFLICT', '个人资料已在别处更新，请重新读取后再保存。')
        if status in (401, 403):
            self.offline = True
            raise AccountError('PROFILE_AUTH_REQUIRED', '暂时无法验证个人资料权限，请在账号入口重新验证。')
        if not 200 <= status < 300:
            if status >= 500:
                self.offline = True
            message = ('头像格式、尺寸或大小不符合要求。' if status in (413, 415, 422)
                       else '个人资料保存失败，请稍后重试。')
            raise AccountError('PROFILE_FAILED', message)

    def _accept(self, raw):
        try:
            value = json.loads(raw)
            _validate(value)
        except (ValueError, TypeError, UnicodeError):
            raise AccountError('PROFILE_INVALID', '服务器的个人资料响应无效。') from None
        with self._lock:
            if value['version'] < self._value['version']:
                return self.snapshot()
        avatar = value.get('avatar')
        uri = ''
        if avatar is not None:
            old = self._value.get('avatar')
            headers = {'If-None-Match': old['etag']} if old and self._value.get('avatar_url') else {}
            status, image_headers, image = self._request('GET', '/avatar', headers=headers)
            if status == 304 and old == avatar:
                uri = self._value['avatar_url']
            else:
                # A concurrent avatar mutation must be read consistently next time.
                self._status(status)
                if image_headers.get('etag') != avatar['etag']:
                    raise AccountError('PROFILE_CONFLICT', '头像已在别处更新，请重新读取。')
                with Image.open(io.BytesIO(image)) as img:
                    if img.format != 'JPEG' or img.width != img.height or img.width > 512:
                        raise AccountError('PROFILE_INVALID', '服务器头像格式无效。')
                    img.verify()
                uri = data_uri(image)
        with self._lock:
            self._session()
            if value['version'] < self._value['version']:
                return self.snapshot()
            self._value = {**value, 'avatar_url': uri}
            _storage(self.account_id, self.data_root).write({'account_id': self.account_id, **self._value})
            self.available, self.offline = True, False
        return self.snapshot()

    def refresh(self):
        try:
            status, _, raw = self._request('GET')
            self._status(status)
            return self._accept(raw)
        except AccountError as exc:
            if exc.code == 'PROFILE_UNSUPPORTED':
                self._value = dict(nickname='', signature='', version=1, avatar=None, avatar_url='')
                self.offline = False
            elif exc.code in ('PROFILE_OFFLINE', 'PROFILE_AUTH_REQUIRED') or (exc.code == 'PROFILE_FAILED' and self.offline):
                self.offline = True
            else:
                raise
            return self.snapshot()

    def save(self, nickname, signature, version):
        _validate(dict(nickname=nickname, signature=signature, version=version))
        return self._mutate('PATCH', '', version, json=dict(nickname=nickname, signature=signature))

    def save_avatar(self, content, version):
        return self._mutate('PUT', '/avatar', version, content=content,
                            headers={'Content-Type': 'image/jpeg'})

    def delete_avatar(self, version):
        return self._mutate('DELETE', '/avatar', version)

    def _mutate(self, method, suffix, version, **kwargs):
        if type(version) is not int or version < 1:
            raise AccountError('PROFILE_INVALID', '请先读取个人资料再保存。')
        headers = kwargs.pop('headers', {})
        headers['If-Match'] = f'"profile-{version}"'
        status, _, raw = self._request(method, suffix, headers=headers, **kwargs)
        self._status(status)
        return self._accept(raw)
