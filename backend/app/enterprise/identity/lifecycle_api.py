"""Candidate-scoped lifecycle wiring over official Keycloak administration."""
import asyncio
from datetime import datetime, timezone
import json
import logging
import secrets
import ssl
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .account_lifecycle import KeycloakAccountAdmin, LifecycleError
from .service_account import ServiceAccountTokenResolver
from .reset_completion import _throttle, _object
from .oidc import IdentityError

PREFIX = '/api/enterprise/auth/lifecycle'
PUBLIC_PATHS = frozenset(PREFIX + '/' + p for p in ('capabilities', 'register', 'forgot'))
router = APIRouter()
MESSAGE = '如果该账号或邮箱可用于找回，我们会发送密码重置邮件，请查看邮箱。'
LOG = logging.getLogger(__name__)


class LifecycleRuntime:
    def __init__(self, config):
        self.expires = datetime.fromisoformat(config['expires_at'])
        if self.expires.tzinfo is None: raise ValueError('Expiry timezone required')
        context = ssl.create_default_context(cafile=config['ca_file'])
        self.resolver = ServiceAccountTokenResolver(config['origin'], config['realm'], config['client_id'], config['secret_file'], verify=context)
        self.admin = KeycloakAccountAdmin(config['origin'], config['realm'], self.resolver, verify=context)
        self.jobs = set()

    @property
    def active(self): return datetime.now(timezone.utc) < self.expires

    async def aclose(self):
        if self.jobs: await asyncio.gather(*self.jobs, return_exceptions=True)
        await self.admin.aclose()
        await self.resolver.aclose()


def from_env(env):
    if env.get('MENTORFLOW_ACCOUNT_MVP_TEST') != '1': return None
    path = Path(env['MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG'])
    if path.is_symlink() or path.stat().st_size > 8192: raise ValueError('Invalid lifecycle configuration')
    config = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_object)
    if set(config) != {'origin', 'realm', 'client_id', 'secret_file', 'ca_file', 'expires_at'}:
        raise ValueError('Invalid lifecycle configuration')
    return LifecycleRuntime(config)


def service(request):
    if request.url.scheme != 'https' or request.headers.get('Origin') or request.cookies or request.headers.get('Authorization'):
        raise IdentityError('LIFECYCLE_TRANSPORT_REQUIRED', '需要安全的原生账号连接。', status=403)
    runtime = getattr(request.app.state, 'enterprise_identity_runtime', None)
    result = getattr(runtime, 'lifecycle', None)
    if result is None or not result.active:
        raise IdentityError('ACCOUNT_ACTION_UNAVAILABLE', '当前账号服务暂未开放此功能。', status=503)
    return result, runtime.native_sessions.verifier


async def body(request, keys):
    if request.headers.get('content-type', '').split(';')[0] != 'application/json': raise ValueError()
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk) > 4096: raise ValueError()
        raw.extend(chunk)
    try:
        value = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(value, dict) or set(value) != keys or any(not isinstance(v, str) for v in value.values()): raise ValueError()
        return value
    finally: raw[:] = b'\0'*len(raw)


@router.get(PREFIX + '/capabilities')
async def capabilities(request: Request):
    try: service(request); enabled = True
    except IdentityError: enabled = False
    return JSONResponse({'register': enabled, 'forgot_password': enabled}, headers={'Cache-Control': 'no-store'})


@router.post(PREFIX + '/register')
async def register(request: Request):
    value = {}
    try:
        svc, _ = service(request)
        _throttle(request.client.host if request.client else 'unknown', 'watch')
        value = await body(request, {'username', 'password', 'email'})
        subject = await svc.admin.create_account(**value)
        await svc.admin.send_verification(subject)
        LOG.info('Official verification email submitted')
        return JSONResponse({'state': 'VERIFY_EMAIL_PENDING', 'message': '验证邮件已发送。请点击邮件中的链接，再返回登录。'}, status_code=201, headers={'Cache-Control':'no-store'})
    except LifecycleError as exc:
        message = '账号已被使用，请换一个账号。' if exc.code == 'ACCOUNT_ALREADY_IN_USE' else '注册暂未完成，请检查输入或稍后重试；如账号已创建，请先查看邮箱。'
        return JSONResponse({'code':exc.code, 'message':message}, status_code=409 if exc.code == 'ACCOUNT_ALREADY_IN_USE' else 400, headers={'Cache-Control':'no-store'})
    except ValueError:
        return JSONResponse({'code':'REGISTRATION_INPUT_INVALID','message':'请检查账号、邮箱和密码。'}, status_code=400)
    finally: value.clear()


@router.post(PREFIX + '/forgot')
async def forgot(request: Request):
    svc, verifier = service(request)
    _throttle(request.client.host if request.client else 'unknown', 'watch')
    try:
        value = await body(request, {'account'})
        name = value['account'].strip()
        if not name or len(name) > 255: raise ValueError()
    except ValueError:
        return JSONResponse({'code':'ACCOUNT_INPUT_INVALID','message':'请输入账号或邮箱。'}, status_code=400)
    if len(svc.jobs) >= 32:
        raise IdentityError('RESET_RATE_LIMITED', '请稍后重试。', status=429)
    from .reset_completion import identity_observation
    observation = await identity_observation(verifier, 'watch', secrets.token_urlsafe(32))
    # Return before provider lookup/send, with identical status/body for existence,
    # provider failure and success. No recipient-specific timing crosses this API.
    async def send():
        try:
            subject = await svc.admin.find_account(name)
            if subject:
                response = await verifier.http.post(verifier.endpoint, json={'operation':'reset-execute-bind',
                    'transaction_id':observation['transaction_id'], 'subject':subject})
                if response.status_code != 200 or response.json() != observation:
                    return
                await svc.admin.send_password_reset(subject)
                LOG.info('Official UPDATE_PASSWORD email submitted')
        except Exception:
            pass  # Anonymous request remains uniform; no provider/credential logs.
    task = asyncio.create_task(send())
    svc.jobs.add(task); task.add_done_callback(svc.jobs.discard)
    return JSONResponse({'state':'RESET_WAITING','message':MESSAGE,'observation':observation}, status_code=202, headers={'Cache-Control':'no-store'})
