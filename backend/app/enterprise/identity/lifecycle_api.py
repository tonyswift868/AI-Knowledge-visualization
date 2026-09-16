"""Candidate-scoped lifecycle wiring over official Keycloak administration."""
import asyncio
from datetime import datetime, timezone, timedelta
import json
import logging
import secrets
import ssl
import re
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .account_lifecycle import KeycloakAccountAdmin, LifecycleError, validate_new_password
from .service_account import ServiceAccountTokenResolver
from .reset_completion import _throttle, _object
from .oidc import IdentityError

PREFIX = '/api/enterprise/auth/lifecycle'
PUBLIC_PATHS = frozenset(PREFIX + '/' + p for p in ('capabilities', 'register', 'forgot',
    'reset-code/request', 'reset-code/verify', 'reset-code/complete'))
router = APIRouter()
MESSAGE = '如果该账号或邮箱可用于找回，我们会发送密码重置邮件，请查看邮箱。'
LOG = logging.getLogger(__name__)


class LifecycleRuntime:
    def __init__(self, config):
        self.expires = datetime.fromisoformat(config['expires_at']) if 'expires_at' in config else None
        if self.expires is not None and self.expires.tzinfo is None: raise ValueError('Expiry timezone required')
        context = ssl.create_default_context(cafile=config['ca_file'])
        if config.get('client_cert_file') and config.get('client_key_file'):
            context.load_cert_chain(config['client_cert_file'], config['client_key_file'])
        self.resolver = ServiceAccountTokenResolver(config['origin'], config['realm'], config['client_id'], config['secret_file'], verify=context)
        self.admin = KeycloakAccountAdmin(config['origin'], config['realm'], self.resolver, verify=context)
        self.jobs = set()

    @property
    def active(self): return self.expires is None or datetime.now(timezone.utc) < self.expires

    async def aclose(self):
        if self.jobs: await asyncio.gather(*self.jobs, return_exceptions=True)
        await self.admin.aclose()
        await self.resolver.aclose()


def from_env(env):
    deployed = env.get('MENTORFLOW_ACCOUNT_LIFECYCLE_ENABLED') == '1'
    test = env.get('MENTORFLOW_ACCOUNT_MVP_TEST') == '1'
    if not deployed and not test: return None
    path = Path(env['MENTORFLOW_ACCOUNT_LIFECYCLE_CONFIG'])
    if path.is_symlink() or path.stat().st_size > 8192: raise ValueError('Invalid lifecycle configuration')
    config = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_object)
    required = {'origin', 'realm', 'client_id', 'secret_file', 'ca_file'}
    if not required.issubset(config) or set(config) - required - {'candidate_sha', 'expires_at', 'client_cert_file', 'client_key_file'}:
        raise ValueError('Invalid lifecycle configuration')
    if ('client_cert_file' in config or 'client_key_file' in config) and (
            not config.get('client_cert_file') or not config.get('client_key_file')):
        raise ValueError('Lifecycle client certificate and key must be paired')
    # Resident Backend opt-in uses its existing service credential and TLS
    # binding. Disposable candidate mode retains the bounded lease contract.
    if test and 'expires_at' not in config:
        raise ValueError('Test lifecycle expiry required')
    candidate = env.get('MENTORFLOW_ACCOUNT_LIFECYCLE_CANDIDATE', '').strip()
    if candidate:
        if not re.fullmatch(r'[0-9a-f]{40,64}', candidate) or config.get('candidate_sha') != candidate:
            raise ValueError('Lifecycle candidate binding mismatch')
    if 'expires_at' in config:
        expiry = datetime.fromisoformat(config['expires_at'])
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError('Lifecycle candidate expired')
        if expiry - datetime.now(timezone.utc) > timedelta(days=7):
            raise ValueError('Lifecycle candidate expiry too long')
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


async def _email_code_authority(verifier, payload):
    try:
        async with asyncio.timeout(10):
            async with verifier.http.stream('POST', verifier.endpoint, json=payload) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(raw)+len(chunk)>2048:raise ValueError()
                    raw.extend(chunk)
                if response.status_code != 200:raise ValueError()
                result = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(result, dict) or not isinstance(result.get('status'), str):
            raise ValueError()
        return result
    except Exception:
        raise IdentityError('RESET_SERVICE_UNAVAILABLE', '重置服务暂不可用，请稍后重试。', status=503) from None


def _reset_error(code, message, status=400):
    return JSONResponse({'code':code, 'message':message}, status_code=status,
                        headers={'Cache-Control':'no-store'})


@router.post(PREFIX + '/reset-code/request')
async def request_email_code(request: Request):
    svc, verifier = service(request)
    _throttle(request.client.host if request.client else 'unknown', 'watch')
    try:
        value = await body(request, {'email'})
        email = value['email'].strip()
        if len(email)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email): raise ValueError()
    except ValueError:
        return _reset_error('RESET_EMAIL_INVALID', '请输入有效的绑定邮箱。')
    if len(svc.jobs)>=32:
        raise IdentityError('RESET_RATE_LIMITED', '请稍后重试。', status=429)
    ticket = secrets.token_urlsafe(32)
    async def send():
        try:
            result = await _email_code_authority(verifier, {'operation':'email-code-request',
                'transaction_id':ticket, 'email':email})
            if result == {'status':'ACCEPTED','delivery':'SUBMITTED'}:
                LOG.info('Password reset code email submitted')
        except IdentityError:
            pass  # Same anonymous response for lookup/send failure and absent users.
    task = asyncio.create_task(send()); svc.jobs.add(task); task.add_done_callback(svc.jobs.discard)
    return JSONResponse({'state':'CODE_SENT', 'transaction_id':ticket, 'expires_in':600,
        'resend_after':60, 'message':'如果该邮箱绑定了可找回账号，我们会发送验证码，请查看邮箱。'},
        status_code=202, headers={'Cache-Control':'no-store'})


@router.post(PREFIX + '/reset-code/verify')
async def verify_email_code(request: Request):
    _, verifier = service(request)
    _throttle(request.client.host if request.client else 'unknown', 'watch')
    value = {}
    try:
        value = await body(request, {'transaction_id', 'code'})
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', value['transaction_id']) or not re.fullmatch(r'[0-9]{6}', value['code']):
            raise ValueError()
        result = await _email_code_authority(verifier, {'operation':'email-code-verify', **value})
        if result.get('status') != 'VERIFIED':
            raise ValueError()
        if set(result) != {'status','reset_proof','expires_in'} or not re.fullmatch(r'[A-Za-z0-9_-]{43}',result['reset_proof']) or result['expires_in']!='300':
            raise IdentityError('RESET_SERVICE_UNAVAILABLE', '重置服务暂不可用，请稍后重试。',status=503)
        return JSONResponse({'state':'VERIFIED', 'reset_proof':result['reset_proof'], 'expires_in':300},
            headers={'Cache-Control':'no-store'})
    except ValueError:
        return _reset_error('RESET_CODE_INVALID_OR_EXPIRED', '验证码错误、已失效或尝试次数过多，请重新获取。')
    finally: value.clear()


@router.post(PREFIX + '/reset-code/complete')
async def complete_email_code(request: Request):
    _, verifier = service(request)
    _throttle(request.client.host if request.client else 'unknown', 'watch')
    value = {}
    try:
        value = await body(request, {'reset_proof','new_password','confirm_password'})
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}',value['reset_proof']):raise ValueError()
        if value['new_password'] != value['confirm_password']:
            return _reset_error('PASSWORD_CONFIRMATION_MISMATCH', '两次输入的密码不一致。')
        try: validate_new_password(value['new_password'])
        except LifecycleError:
            return _reset_error('PASSWORD_POLICY_REJECTED', '密码须为 8–128 位，并符合账号密码规则。')
        result = await _email_code_authority(verifier, {'operation':'email-code-complete',
            'reset_proof':value['reset_proof'], 'new_password':value['new_password']})
        status = result.get('status')
        if status == 'PENDING_COMMIT':
            result = await _email_code_authority(verifier, {'operation':'email-code-status',
                'reset_proof':value['reset_proof']})
            status = result.get('status')
            if status != 'COMPLETED':
                raise IdentityError('RESET_SERVICE_UNAVAILABLE','重置尚未确认，请重新获取验证码。',status=503)
        if status == 'SAME_CURRENT_PASSWORD':
            return _reset_error('NEW_PASSWORD_SAME_AS_CURRENT', '新密码不能与当前密码相同')
        if status == 'PASSWORD_POLICY_REJECTED':
            return _reset_error('PASSWORD_POLICY_REJECTED', '密码不符合账号密码规则，请修改后重试。')
        if status == 'INVALID':raise ValueError()
        if status != 'COMPLETED' or set(result)!={'status','subject'} or not isinstance(result['subject'],str) or not 1<=len(result['subject'])<=128:
            raise IdentityError('RESET_SERVICE_UNAVAILABLE','重置服务暂不可用，请稍后重试。',status=503)
        from app.account_identity_contract import account_id
        runtime = request.app.state.enterprise_identity_runtime
        runtime.native_sessions.revoke_subject(result['subject'])
        return JSONResponse({'state':'COMPLETED', 'account_id':account_id(runtime.native_sessions.issuer,result['subject']),
            'message':'密码已重置，请使用新密码登录 MentorFlow。'}, headers={'Cache-Control':'no-store'})
    except ValueError:
        return _reset_error('RESET_PROOF_INVALID_OR_EXPIRED', '重置验证已失效，请重新获取验证码。')
    finally: value.clear()


@router.post(PREFIX + '/change-password')
async def change_password(request: Request):
    """Authenticated self-service change; identity comes from the validated actor."""
    runtime = getattr(request.app.state, 'enterprise_identity_runtime', None)
    svc = getattr(runtime, 'lifecycle', None)
    if runtime is None or svc is None or not svc.active:
        raise IdentityError('ACCOUNT_ACTION_UNAVAILABLE', '当前账号服务暂未开放此功能。', status=503)
    if (request.url.scheme != 'https' or request.headers.get('Origin') or request.cookies
            or not request.headers.get('Authorization', '').startswith('Bearer mfn1_a_')):
        raise IdentityError('LIFECYCLE_TRANSPORT_REQUIRED', '需要安全的原生账号连接。', status=403)
    binding = await runtime.revalidate_product_identity(request)
    actor = binding.context.actor if binding.context else None
    # Bearer bindings need no browser/server session. Revalidation above owns
    # token validity and membership; credential reauthentication below must
    # still match this exact actor before any credential update.
    if actor is None:
        raise IdentityError('ENTERPRISE_SESSION_REQUIRED', '请先登录。', status=401)
    try:
        value = await body(request, {'current_password', 'new_password'})
        if not value['current_password'] or len(value['current_password']) > 1024:
            raise ValueError()
        validate_new_password(value['new_password'])
        runtime.native_sessions.throttle(actor.subject_id, request.client.host if request.client else 'unknown')
        username = await svc.admin.username_for_subject(actor.subject_id)
        try:
            principal = await runtime.native_sessions.verifier.authenticate(username, value['current_password'])
        except IdentityError as exc:
            if exc.code != 'NATIVE_CREDENTIALS_INVALID': raise
            return JSONResponse({'code': 'CURRENT_PASSWORD_INVALID', 'message': '当前密码不正确。'},
                                status_code=400, headers={'Cache-Control': 'no-store'})
        if (principal.issuer, principal.subject) != (actor.issuer, actor.subject_id):
            raise IdentityError('NATIVE_CREDENTIALS_INVALID', '账号或密码不正确', status=401)
        await svc.admin.change_password(actor.subject_id, value['new_password'])
        return JSONResponse({'state': 'PASSWORD_CHANGED'}, headers={'Cache-Control': 'no-store'})
    except ValueError:
        return JSONResponse({'code': 'PASSWORD_INPUT_INVALID', 'message': '请检查密码长度。'}, status_code=400,
                            headers={'Cache-Control': 'no-store'})
    except LifecycleError as exc:
        if exc.code == 'PASSWORD_INPUT_INVALID':
            return JSONResponse({'code': exc.code, 'message': '请检查密码长度。'}, status_code=400,
                                headers={'Cache-Control': 'no-store'})
        raise IdentityError('ACCOUNT_ACTION_UNAVAILABLE', '密码暂未修改，请稍后重试。', status=503)
    finally:
        value = locals().get('value')
        if isinstance(value, dict): value.clear()
