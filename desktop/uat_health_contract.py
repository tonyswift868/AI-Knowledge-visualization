"""TEST-ONLY signed attempt health. Never grants installed/update authority.

The UAT-only frozen entry supplies a build-injected fixture public key. Normal
desktop entry does not import this module or consume its environment variable.
Receipt validation and commit/healthy transitions remain helper responsibilities.
"""
import base64
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import re
import sys
import time

from .update_protocol import SecurityError, canonical, digest, strict_json, version_key

REQUEST_FIELDS = {'schema_version', 'app_id', 'attempt_id', 'nonce', 'purpose',
    'created_at', 'expires_at', 'expected_app_root', 'root_identity',
    'runtime_version', 'target_manifest_sha256', 'executable_sha256',
    'signed_release_identity', 'receipt_sha256', 'health_endpoint',
    'fixture_root', 'cross_volume_root'}
HEALTH_FIELDS = {'schema_version', 'app_id', 'attempt_id', 'nonce',
    'runtime_version', 'target_manifest_sha256', 'signed_release_identity',
    'receipt_sha256', 'process_id', 'timestamp', 'qt_ready', 'marks',
    'actual_app_root', 'root_identity', 'token', 'installed_health_authority'}


def verify_request(raw, keys, *, now=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    now = time.time() if now is None else now
    try:
        doc = strict_json(raw)
        if set(doc) != {'signed', 'key_id', 'signature'} or doc['key_id'] not in keys:
            raise SecurityError('unknown UAT fixture signer')
        Ed25519PublicKey.from_public_bytes(keys[doc['key_id']]).verify(
            base64.b64decode(doc['signature'], validate=True), canonical(doc['signed']))
        request = doc['signed']
        if set(request) != REQUEST_FIELDS or type(request['schema_version']) is not int or request['schema_version'] != 1 or request['app_id'] != 'MentorFlow':
            raise SecurityError('invalid UAT request schema')
        if request['purpose'] not in {'boot-health', 'legacy-benchmark'}:
            raise SecurityError('invalid UAT purpose')
        for key, length in [('attempt_id', 32), ('nonce', 64), ('target_manifest_sha256', 64), ('executable_sha256', 64), ('signed_release_identity', 64)]:
            if not isinstance(request[key], str) or not re.fullmatch('[0-9a-f]{%d}' % length, request[key]):
                raise SecurityError('invalid UAT identity: ' + key)
        if request['receipt_sha256'] is not None and not re.fullmatch('[0-9a-f]{64}', request['receipt_sha256']):
            raise SecurityError('invalid receipt pin')
        version_key(request['runtime_version'])
        created, expires = request['created_at'], request['expires_at']
        if type(created) not in (int, float) or type(expires) not in (int, float) or not created <= now <= expires or not 0 < expires-created <= 120:
            raise SecurityError('stale UAT attempt')
        if not isinstance(request['root_identity'], list) or len(request['root_identity']) != 2 or any(type(n) is not int or n < 0 for n in request['root_identity']):
            raise SecurityError('invalid UAT root identity')
        from .update_tree_security import normal
        app = normal(request['expected_app_root'])
        endpoint = normal(request['health_endpoint'])
        if app.name != 'App' or endpoint.name != 'health.json' or endpoint.parent.name != request['attempt_id'] or endpoint.is_relative_to(app.parent):
            raise SecurityError('invalid per-attempt health location')
        for key in ('fixture_root', 'cross_volume_root'):
            if request[key] is not None:
                normal(request[key])
        return request
    except (InvalidSignature, ValueError, TypeError, KeyError, AttributeError) as error:
        raise SecurityError('invalid signed UAT request') from error


def validate_health(health, request, *, expected_pid, now=None):
    now = time.time() if now is None else now
    if not isinstance(health, dict) or set(health) != HEALTH_FIELDS:
        raise SecurityError('invalid health schema')
    for key in ('schema_version', 'app_id', 'attempt_id', 'nonce', 'runtime_version',
                'target_manifest_sha256', 'signed_release_identity', 'receipt_sha256', 'root_identity'):
        if health[key] != request[key]:
            raise SecurityError('health binding mismatch: ' + key)
    if type(expected_pid) is not int or expected_pid <= 0 or type(health['process_id']) is not int or health['process_id'] != expected_pid:
        raise SecurityError('health PID mismatch')
    if type(health['timestamp']) not in (int, float) or not request['created_at'] <= health['timestamp'] <= now <= request['expires_at']:
        raise SecurityError('stale health marker')
    if health['actual_app_root'] != request['expected_app_root'] or health['qt_ready'] is not True or health['installed_health_authority'] is not False:
        raise SecurityError('invalid health authority/readiness')
    if health['token'] != {'integrity': 'MEDIUM', 'elevated': False, 'integrity_rid': 8192}:
        raise SecurityError('non-Medium target')
    marks = health['marks']
    names = ('PYTHON_ENTRY', 'QT_IMPORT', 'QAPPLICATION', 'QT_EVENT_LOOP_READY')
    if not isinstance(marks, dict) or set(marks) != set(names):
        raise SecurityError('incomplete Qt health')
    values = [marks[k] for k in names]
    if any(type(v) not in (int, float) or not 0 <= v <= 120000 for v in values) or values != sorted(values):
        raise SecurityError('invalid Qt marks')
    return True


def current_token():
    kernel, adv = c.WinDLL('kernel32', use_last_error=True), c.WinDLL('advapi32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)]
    adv.GetSidSubAuthorityCount.argtypes = [c.c_void_p]
    adv.GetSidSubAuthorityCount.restype = c.POINTER(c.c_ubyte)
    adv.GetSidSubAuthority.argtypes = [c.c_void_p, w.DWORD]
    adv.GetSidSubAuthority.restype = c.POINTER(w.DWORD)
    token, needed, elevated = w.HANDLE(), w.DWORD(), w.DWORD()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        if not adv.GetTokenInformation(token, 20, c.byref(elevated), 4, c.byref(needed)):
            raise c.WinError(c.get_last_error())
        adv.GetTokenInformation(token, 25, None, 0, c.byref(needed))
        buf = c.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 25, buf, len(buf), c.byref(needed)):
            raise c.WinError(c.get_last_error())
        sid = c.cast(buf, c.POINTER(c.c_void_p)).contents.value
        rid = adv.GetSidSubAuthority(sid, adv.GetSidSubAuthorityCount(sid).contents.value-1).contents.value
        return dict(integrity='MEDIUM' if rid == 8192 else 'OTHER', elevated=bool(elevated.value), integrity_rid=rid)
    finally:
        kernel.CloseHandle(token)


def context():
    if not getattr(sys, 'frozen', False):
        raise SecurityError('frozen UAT build required')
    # This generated module exists only in the explicit UAT spec, never the
    # production analysis. An environment variable cannot create this marker.
    from mentorflow_uat_build_authority import BUILD_MARKER, PUBLIC_KEYS
    if BUILD_MARKER != 'mentorflow.test-only.signed-attempt.v1':
        raise SecurityError('UAT build marker missing')
    from .update_tree_security import DirectoryPins, normal
    from scripts.runtime_path_budget import budget
    exe = normal(sys.executable)
    app = exe.parent
    if exe.name != 'MentorFlow.exe' or app.name != 'App' or normal(sys._MEIPASS) != app/'_internal':
        raise SecurityError('invalid ONEDIR bundle layout')
    request_path = normal(os.environ.get('MENTORFLOW_UAT_ATTEMPT', ''))
    with DirectoryPins() as pins:
        pins.check(request_path, request_path.parent, missing=False)
        if request_path.stat().st_size > 65536:
            raise SecurityError('oversized UAT request')
        request = verify_request(request_path.read_bytes(), PUBLIC_KEYS)
        if request_path.name != 'request.json' or request_path.parent != Path(request['health_endpoint']).parent:
            raise SecurityError('request/health attempt directory mismatch')
        pins.check(app, app, directory=True, missing=False)
        if str(app) != request['expected_app_root'] or [app.stat().st_dev, app.stat().st_ino] != request['root_identity']:
            raise SecurityError('signed UAT bundle identity mismatch')
        from .update_protocol import MANIFEST_PATH
        manifest_path = app/MANIFEST_PATH
        pins.check(manifest_path, app, missing=False)
        raw = manifest_path.read_bytes()
        if digest(raw) != request['target_manifest_sha256']:
            raise SecurityError('UAT manifest mismatch')
        manifest = strict_json(raw)
        if manifest.get('product') != request['app_id'] or manifest.get('version') != request['runtime_version']:
            raise SecurityError('UAT manifest identity mismatch')
        budget(app.parent, [row['path'] for row in manifest['files']])
        import hashlib
        # Only the EXE and manifest are launch bindings here, NOT full-package
        # commit verification. Elevated helper must reverify all target bytes.
        with exe.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != request['executable_sha256']:
                raise SecurityError('UAT executable mismatch')
        if request['receipt_sha256'] is not None:
            receipt = app.parent/'installation-receipt.json'
            pins.check(receipt, app.parent, missing=False)
            if digest(receipt.read_bytes()) != request['receipt_sha256']:
                raise SecurityError('UAT receipt pin mismatch')
        pins.check(Path(request['health_endpoint']).parent, request_path.parent, directory=True, missing=False)
        if Path(request['health_endpoint']).exists():
            raise SecurityError('health marker already exists')
    token = current_token()
    if token != dict(integrity='MEDIUM', elevated=False, integrity_rid=8192):
        raise SecurityError('standard-user UAT required')
    return request, app, token


def startup(python_entry):
    request, app_root, token = context()
    if request['purpose'] != 'boot-health':
        raise SecurityError('wrong UAT startup purpose')
    marks = dict(PYTHON_ENTRY=0)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    marks['QT_IMPORT'] = (time.monotonic()-python_entry)*1000
    from desktop.update_panel import UpdatePanel
    from desktop.version import VERSION_MANIFEST
    from scripts import windows_update_apply
    if VERSION_MANIFEST['version'] != request['runtime_version']:
        raise SecurityError('UAT runtime version mismatch')
    app = QApplication([str(app_root/'MentorFlow.exe')])
    marks['QAPPLICATION'] = (time.monotonic()-python_entry)*1000
    panel = UpdatePanel(service=None)
    panel.show()
    outcome = [1]
    def ready():
        try:
            if not panel.isVisible():
                raise SecurityError('UAT window not ready')
            marks['QT_EVENT_LOOP_READY'] = (time.monotonic()-python_entry)*1000
            health = {key: request[key] for key in ('schema_version', 'app_id', 'attempt_id', 'nonce', 'runtime_version', 'target_manifest_sha256', 'signed_release_identity', 'receipt_sha256', 'root_identity')}
            health.update(process_id=os.getpid(), timestamp=time.time(), qt_ready=True,
                marks=marks, actual_app_root=str(app_root), token=token, installed_health_authority=False)
            validate_health(health, request, expected_pid=os.getpid())
            from .update_tree_security import DirectoryPins
            endpoint = Path(request['health_endpoint'])
            with DirectoryPins() as pins:
                pins.check(endpoint.parent, endpoint.parent, directory=True, missing=False)
                with endpoint.open('xb') as stream:
                    stream.write(canonical(health)); stream.flush(); os.fsync(stream.fileno())
            outcome[0] = 0
        finally:
            panel.shutdown(); panel.close(); app.quit()
    QTimer.singleShot(200, ready)
    QTimer.singleShot(45000, app.quit)
    app.exec()
    return outcome[0]
