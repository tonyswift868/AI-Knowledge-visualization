"""Bounded, PII-free application administrator overview; no lifecycle writes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import ssl
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import ARRAY, String, bindparam, text

from .account_lifecycle import KeycloakAccountAdmin
from .application_roles import ApplicationRole, reject_client_role_assertions
from .oidc import IdentityError
from .service_account import ServiceAccountTokenResolver

COUNTERS = ('registered_human_users', 'registrations_today', 'registrations_7d',
            'registrations_30d', 'enabled_users', 'disabled_users', 'email_verified_users',
            'email_unverified_users', 'required_action_users', 'backend_account_count',
            'workspace_member_count', 'identity_account_mismatch_count')
TIMEOUT_SECONDS = 6


class KeycloakOverviewReader:
    """Read-only use of the accepted backend Admin API/token resolver."""
    PAGE_SIZE = 50
    MAX_USERS = 100_000

    def __init__(self, admin, resolver=None, *, lifecycle=None):
        self.admin, self.resolver = admin, resolver
        self.lifecycle = lifecycle

    async def users(self):
        if self.lifecycle is not None and not self.lifecycle.active:
            raise ValueError('IDENTITY_READER_AUTHORITY_EXPIRED')
        result, seen = [], set()
        for first in range(0, self.MAX_USERS + 1, self.PAGE_SIZE):
            page, _ = await self.admin._call('GET', '/users', expected=200,
                params={'first': first, 'max': self.PAGE_SIZE, 'briefRepresentation': 'false'})
            if not isinstance(page, list) or len(page) > self.PAGE_SIZE:
                raise ValueError('INVALID_IDENTITY_PAGE')
            for item in page:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id']:
                    raise ValueError('INVALID_IDENTITY_PAGE')
                if item['id'] in seen:
                    # A changing/duplicated page cannot be presented as an exact total.
                    raise ValueError('IDENTITY_PAGE_CHANGED')
                seen.add(item['id'])
                if len(seen) > self.MAX_USERS:
                    raise ValueError('IDENTITY_PAGE_LIMIT')
                service_account = bool(item.get('serviceAccountClientId'))
                if service_account:
                    continue
                if (type(item.get('enabled')) is not bool or type(item.get('emailVerified')) is not bool
                        or type(item.get('createdTimestamp')) is not int
                        or not isinstance(item.get('requiredActions', []), list)):
                    raise ValueError('IDENTITY_REPRESENTATION_INCOMPLETE')
                # Discard names, email addresses, attributes and credentials immediately.
                result.append({key: item.get(key, []) for key in
                    ('id', 'createdTimestamp', 'enabled', 'emailVerified', 'requiredActions')})
            if len(page) < self.PAGE_SIZE:
                return result
        raise ValueError('IDENTITY_PAGE_LIMIT')

    async def aclose(self):
        if self.lifecycle is None:
            await self.admin.aclose()
        if self.resolver is not None:
            await self.resolver.aclose()


def aggregate(users, now):
    subjects, created = set(), []
    for item in users:
        if item.get('service_account') or item.get('serviceAccountClientId'):
            continue
        if item['id'] in subjects:
            raise ValueError('DUPLICATE_IDENTITY')
        subjects.add(item['id'])
        created.append(datetime.fromtimestamp(item['createdTimestamp'] / 1000, timezone.utc))
    humans = [u for u in users
              if not u.get('service_account') and not u.get('serviceAccountClientId')]
    enabled = sum(u['enabled'] for u in humans)
    verified = sum(u['emailVerified'] for u in humans)
    return dict(registered_human_users=len(subjects),
        registrations_today=sum(t.date() == now.date() and t <= now for t in created),
        registrations_7d=sum(now - timedelta(days=7) <= t <= now for t in created),
        registrations_30d=sum(now - timedelta(days=30) <= t <= now for t in created),
        enabled_users=enabled, disabled_users=len(subjects)-enabled,
        email_verified_users=verified, email_unverified_users=len(subjects)-verified,
        required_action_users=sum(bool(u.get('requiredActions')) for u in humans)), subjects


class AdminOverviewService:
    def __init__(self, engine, issuer, reader=None, registry=None):
        self.engine, self.issuer, self.reader, self.registry = engine, issuer, reader, registry
        self._lock = asyncio.Lock()

    def _backend_counts(self, subjects):
        # The narrow scalar function is the only global read seam. The app role
        # remains NOBYPASSRLS, with no global SELECT privilege or second account DB.
        with self.engine.begin() as connection:
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text("SET LOCAL statement_timeout = '2000ms'"))
            statement = text('SELECT public.mentorflow_admin_account_counts('
                ':issuer, :subjects)').bindparams(bindparam('subjects', type_=ARRAY(String)))
            value = connection.execute(statement,
                dict(issuer=self.issuer, subjects=sorted(subjects))).scalar_one()
            if not isinstance(value, dict):
                raise ValueError('ADMIN_AGGREGATE_INVALID')
            return value

    def _health(self):
        if self.registry is None:
            return {}
        result = self.registry.readiness()
        health = {'backend': {'status': 'healthy' if result['started'] else 'unavailable',
                              'code': 'RUNTIME_STARTED' if result['started'] else 'RUNTIME_STARTING'}}
        for item in result['dependencies']:
            name = {'postgres': 'database', 'keycloak_discovery': 'keycloak', 'email': 'email'}.get(item['name'])
            if name:
                health[name] = {'status': 'healthy' if item['ready'] else 'unavailable', 'code': item['code']}
        return health

    async def collect(self):
        # Coalesce access under a lock and refuse a second expensive concurrent
        # traversal. The UI keeps the last successful sample during busy/outage.
        if self._lock.locked():
            raise IdentityError('ADMIN_OVERVIEW_BUSY', 'Overview is refreshing', status=503)
        async with self._lock:
            now = datetime.now(timezone.utc)
            payload = {key: None for key in COUNTERS}
            services = {name: {'status': 'unknown', 'code': 'NOT_OBSERVED'}
                        for name in ('keycloak', 'backend', 'database', 'email')}
            services['email'] = {'status': 'unavailable', 'code': 'EMAIL_AUTHORITY_UNAVAILABLE'}
            if self.registry is not None:
                services['backend'] = {
                    'status': 'healthy' if self.registry.started else 'unavailable',
                    'code': 'RUNTIME_STARTED' if self.registry.started else 'RUNTIME_STARTING',
                }
            async def observe_health():
                # The SMTP probe performs several bounded TLS/AUTH round trips.
                # Keep it inside the route's ten-second overall deadline without
                # discarding a healthy relay solely at the former short budget.
                try:
                    return await asyncio.wait_for(asyncio.to_thread(self._health), 7)
                except Exception:
                    return {}

            async def observe_users():
                if self.reader is None:
                    return None
                try:
                    return await asyncio.wait_for(self.reader.users(), TIMEOUT_SECONDS)
                except Exception:
                    return None

            # Independent observations share the route deadline, rather than
            # accumulating SMTP + Admin API waits before the database read.
            health, users = await asyncio.gather(observe_health(), observe_users())
            services.update(health)
            subjects = None
            statistics = {'status': 'unavailable', 'code': 'ADMIN_READER_NOT_CONFIGURED'}
            if self.reader is not None:
                try:
                    if users is None:
                        raise ValueError('ADMIN_READ_FAILED')
                    counters, subjects = aggregate(users, now)
                    payload.update(counters)
                    services['keycloak'] = {'status': 'healthy', 'code': 'ADMIN_READ_OK'}
                    statistics = {'status': 'healthy', 'code': 'ADMIN_READ_OK'}
                except Exception:
                    statistics = {'status': 'unavailable', 'code': 'ADMIN_READ_FAILED'}
                    if services['keycloak']['status'] != 'healthy':
                        services['keycloak'] = statistics.copy()
            # Missing/denied Admin API authority is a statistics failure; it
            # must not erase a successfully observed discovery dependency.
            try:
                counts = await asyncio.wait_for(asyncio.to_thread(self._backend_counts, subjects or set()), 2.5)
                payload.update(counts)
                if subjects is None:
                    payload['identity_account_mismatch_count'] = None
                services['database'] = {'status': 'healthy', 'code': 'CANONICAL_AGGREGATE_OK'}
            except Exception:
                services['database'] = {'status': 'unavailable', 'code': 'CANONICAL_AGGREGATE_UNAVAILABLE'}
            complete = all(payload[key] is not None for key in COUNTERS)
            payload.update(schema='mentorflow.admin-overview.v1', services=services,
                data_status='current' if complete else 'unavailable',
                refreshed_at=now.isoformat() if complete else None, observed_at=now.isoformat(),
                timezone='UTC', refresh_seconds=60, identity_statistics=statistics)
            return payload

    async def aclose(self):
        if self.reader is not None:
            await self.reader.aclose()


def from_env(engine, identity_config, registry, env, *, lifecycle=None):
    """Prefer a dedicated reader; reuse an already approved admin connection.

    The borrowed connection only issues GET /users and remains owned by identity
    runtime. Dedicated reader configuration works without public lifecycle.
    """
    reader = None
    path = env.get('MENTORFLOW_ADMIN_OVERVIEW_CONFIG')
    if path:
        path = Path(path)
        if not path.is_absolute() or path.is_symlink() or path.stat().st_size > 8192:
            raise ValueError('Invalid overview reader configuration')
        config = json.loads(path.read_text(encoding='utf-8'))
        required = {'origin', 'realm', 'client_id', 'secret_file', 'ca_file'}
        optional = {'client_cert_file', 'client_key_file'}
        if not required.issubset(config) or set(config) - required - optional:
            raise ValueError('Invalid overview reader configuration')
        if bool(config.get('client_cert_file')) != bool(config.get('client_key_file')):
            raise ValueError('Overview mTLS requires certificate and key references')
        expected = urlsplit(identity_config.keycloak._transport_authority)
        if (config['origin'].rstrip('/') + '/realms/' + config['realm'] !=
                f'{expected.scheme}://{expected.netloc}{expected.path}'):
            raise ValueError('Overview reader must use the configured identity realm')
        context = ssl.create_default_context(cafile=config['ca_file'] or None)
        if config.get('client_cert_file'):
            context.load_cert_chain(config['client_cert_file'], config['client_key_file'])
        resolver = ServiceAccountTokenResolver(config['origin'], config['realm'],
            config['client_id'], config['secret_file'], verify=context)
        reader = KeycloakOverviewReader(KeycloakAccountAdmin(config['origin'], config['realm'],
            resolver, verify=context), resolver)
    elif lifecycle is not None and lifecycle.active:
        # Internal mTLS origin may differ, but never aggregate another realm.
        realm_path = urlsplit(identity_config.keycloak._transport_authority).path.rstrip('/')
        admin_path = urlsplit(lifecycle.admin.base).path.rstrip('/')
        if not realm_path.startswith('/realms/') or admin_path != '/admin' + realm_path:
            raise ValueError('Overview reader must use the configured identity realm')
        reader = KeycloakOverviewReader(lifecycle.admin, lifecycle=lifecycle)
    return AdminOverviewService(engine, identity_config.keycloak.canonical_identity_issuer, reader, registry)


router = APIRouter(prefix='/api/enterprise/auth', tags=['enterprise-admin-overview'])


@router.get('/admin/overview')
async def overview(request: Request):
    from .runtime import _runtime
    reject_client_role_assertions(request)
    runtime = _runtime(request)
    binding = await runtime.revalidate_product_identity(request)
    if runtime.application_roles.role_for(binding.context.actor) is not ApplicationRole.SUPER_ADMIN:
        raise IdentityError('APPLICATION_SUPER_ADMIN_REQUIRED', 'Product administrator access is required', status=403)
    service = getattr(request.app.state, 'admin_overview_service', None)
    if service is None:
        raise IdentityError('ADMIN_OVERVIEW_UNAVAILABLE', 'Administrator overview is unavailable', status=503)
    try:
        payload = await asyncio.wait_for(service.collect(), 10)
    except asyncio.TimeoutError:
        raise IdentityError('ADMIN_OVERVIEW_TIMEOUT', 'Administrator overview timed out', status=503)
    return JSONResponse(payload, headers={'Cache-Control': 'no-store'})
