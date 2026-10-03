"""Authenticated snapshot transport for course sync and session checks."""
from __future__ import annotations

import json

from pathlib import Path

import re

import ssl

from threading import Event

from urllib.parse import urlsplit

import httpx

from refactor.learning.sync_identity import PlanError
from refactor.learning.sync_identity import PlanScope

SNAPSHOT_PATH = "/api/enterprise/sync/v1/snapshot"

MAX_SNAPSHOT_BYTES = 160 * 1024 * 1024

TRANSIENT_SESSION_CODES = frozenset({'SESSION_UNAVAILABLE', 'SOURCE_UNAVAILABLE', 'SYNC_UNAVAILABLE',
    'ENTERPRISE_ACCOUNT_STATE_UNAVAILABLE', 'ENTERPRISE_OIDC_UNAVAILABLE', 'ENTERPRISE_NATIVE_UNAVAILABLE',
    'NATIVE_NETWORK_UNAVAILABLE', 'NATIVE_AUTH_UNAVAILABLE'})
CONNECTION_UNSTABLE = '账号连接不稳，正在重试；当前学习不受影响'
NETWORK_RETRY_MESSAGE = '网络不稳，请稍后再试'

def sync_auth_error(response, message):
    """Keep the native eviction reason available to SessionGuard.note_error."""
    if response.status_code == 401:
        try:
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=8192):
                raw.extend(chunk)
                if len(raw) > 8192:
                    return PlanError("REAUTH_REQUIRED", message)
            value = json.loads(raw)
            error = value.get("error", value)
            if isinstance(error, dict) and error.get("code") == "NATIVE_SESSION_SUPERSEDED":
                return PlanError("NATIVE_SESSION_SUPERSEDED", "账号已在另一台设备登录")
        except (ValueError, AttributeError, httpx.HTTPError, RecursionError):
            pass
    return PlanError("REAUTH_REQUIRED", message)

def validate_snapshot(snapshot, scope):
    """Validate provenance independently of the old learning-ready label.

    Unknown/partially supported historical formats remain in the snapshot.
    They must not block supported records or be mistaken for an empty account.
    """
    try:
        if (snapshot.get("schema") != "mentorflow.structured-sync.v1"
                or snapshot.get("contract_version") != 3
                or snapshot.get("complete") is not True
                or snapshot.get("snapshot_authority") != "POSTGRESQL_SINGLE_STATEMENT_MVCC"
                or snapshot.get("snapshot_scope") != scope.snapshot_scope()
                or not re.fullmatch(r"pg-snapshot:\d+:\d+:(?:\d+(?:,\d+)*)?", snapshot.get("snapshot_revision", ""))
                or snapshot["observation"].get("source") != "SERVER_CONFIRMED"
                or snapshot["observation"].get("snapshot_revision") != snapshot["snapshot_revision"]):
            raise ValueError()
        seen = set()
        if not isinstance(snapshot["entities"], list):
            raise ValueError()
        for entity in snapshot["entities"]:
            key = (entity["kind"], entity["entity_id"])
            if (key in seen or not all(isinstance(v, str) and v for v in key)
                    or type(entity["revision"]) is not int or entity["revision"] < 1
                    or type(entity["deleted"]) is not bool or not isinstance(entity["payload"], dict)):
                raise ValueError()
            seen.add(key)
        return snapshot
    except (KeyError, TypeError, ValueError, AttributeError):
        raise PlanError("SCOPE_OR_AUTHORITY_MISMATCH", "学习记录的账号、空间或完整性不匹配，已停止同步。") from None

class AuthenticatedPlanSource:

    def __init__(self, *, account_id, session_provider, api_base_url, ca_bundle: Path, http_transport=None):
        url = urlsplit(api_base_url)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('计划服务必须使用已配置的 HTTPS 账号地址')
        self.account_id = account_id
        self._session_provider = session_provider
        self._base_url = api_base_url.rstrip('/')
        self._ssl = ssl.create_default_context(cafile=str(ca_bundle))
        self._transport = http_transport
        self._closed = Event()
        self._scope = None

    def close(self):
        self._closed.set()

    def _session(self):
        if self._closed.is_set():
            raise PlanError('CANCELLED', '账号计划会话已关闭。')
        result = self._session_provider()
        try:
            identity, state = (result.identity, result.server_state)
            account, context = (state['account'], state['context'])
            scope = PlanScope(identity.account_id, identity.issuer, identity.subject, identity.organization_id, identity.workspace_id)
            if scope.account_id != self.account_id or state['authenticated'] is not True or account['account_id'] != scope.account_id or (account['subject_id'] != scope.subject_id) or (context['organization_id'] != scope.organization_id) or (context['workspace_id'] != scope.workspace_id) or any((not isinstance(value, str) or not value or any((ord(c) < 32 for c in value)) for value in scope.snapshot_scope().values())) or (not isinstance(result.access_token, str)) or (not result.access_token):
                raise ValueError()
            if self._scope is not None and scope != self._scope:
                raise ValueError()
            return (scope, result.access_token)
        except (AttributeError, KeyError, TypeError, ValueError):
            raise PlanError('SESSION_UNAVAILABLE', NETWORK_RETRY_MESSAGE) from None

    def require_current(self):
        return self._session()[0]

    def read_snapshot(self):
        scope, token = self._session()
        self._scope = scope
        headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json', 'X-MentorFlow-Organization-ID': scope.organization_id, 'X-MentorFlow-Workspace-ID': scope.workspace_id}
        try:
            with httpx.Client(verify=self._ssl, timeout=20, follow_redirects=False, transport=self._transport) as client:
                with client.stream('GET', self._base_url + SNAPSHOT_PATH, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise sync_auth_error(response, '计划读取的登录状态已失效，请使用账号入口重新验证。')
                    if response.status_code != 200:
                        raise PlanError('SOURCE_UNAVAILABLE', '服务器计划接口暂不可用，当前计划尚未确认，请稍后重试。')
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        self.require_current()
                        raw.extend(chunk)
                        if len(raw) > MAX_SNAPSHOT_BYTES:
                            raise PlanError('SNAPSHOT_TOO_LARGE', '服务器计划快照超过既有接口上限，未截断后显示。')
            self.require_current()

            def unique_pairs(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError('duplicate field')
                    value[key] = item
                return value
            snapshot = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_pairs)
            validate_snapshot(snapshot, scope)
            self.require_current()
            return snapshot
        except PlanError:
            raise
        except (httpx.HTTPError, OSError):
            raise PlanError('SOURCE_UNAVAILABLE', '计划读取失败，请检查网络或服务器状态后重试。') from None
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise PlanError('INVALID_SNAPSHOT', '计划服务返回的数据无法验证。') from None
        finally:
            token = ''
            headers.clear()
