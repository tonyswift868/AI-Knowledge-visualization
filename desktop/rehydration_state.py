"""Presentation-only cloud recovery state. No sync, binding or pack executor."""
from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class SyncState(str, Enum):
    AUTH_SUCCESS = 'AUTH_SUCCESS'
    SYNCING = 'SYNCING'
    SYNC_COMPLETE = 'SYNC_COMPLETE'
    SYNC_OFFLINE = 'SYNC_OFFLINE'
    SYNC_FAILED = 'SYNC_FAILED'
    NOT_AUTHORIZED = 'NOT_AUTHORIZED'


def text(value):
    if not isinstance(value, str):
        raise ValueError('Invalid display text')
    return value


def count(value):
    if type(value) is not int or value < 0:
        raise ValueError('Invalid display count')
    return value


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    progress: str


@dataclass(frozen=True)
class Resource:
    key: str
    plan_key: str
    name: str
    kind: str
    missing: bool
    verified_redownload: bool
    package_supported: bool
    plan_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class CloudView:
    account_key: str
    revision: str
    plans: tuple[Plan, ...]
    resources: tuple[Resource, ...]
    progress_count: int
    review_count: int
    history_count: int
    empty_account: bool
    server_confirmed: bool
    trusted_cache: bool
    last_sync: str
    empty_state: str = ''
    supported_scope: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, Mapping) or raw.get('schema') != 'mentorflow.rehydration-ui.v1':
            raise ValueError('Unknown presentation contract')
        plans = tuple(Plan(text(p['key']), text(p['name']), text(p['progress'])) for p in raw['plans'])
        keys = {p.key for p in plans}
        if '' in keys or len(keys) != len(plans):
            raise ValueError('Invalid plan identities')
        resources = []
        for r in raw['resources']:
            if r['kind'] not in ('file', 'model', 'other', 'unknown') or type(r['missing']) is not bool:
                raise ValueError('Invalid resource state')
            item = Resource(text(r['key']), text(r['plan_key']), text(r['name']), r['kind'], r['missing'],
                            r.get('verified_redownload') is True, r.get('package_supported') is True,
                            tuple(text(k) for k in r.get('plan_keys', [])))
            if len(set(item.plan_keys)) != len(item.plan_keys) or any(k not in keys for k in item.plan_keys):
                raise ValueError('Unknown shared plan binding')
            if item.plan_key and item.plan_key not in keys:
                raise ValueError('Unknown plan binding')
            resources.append(item)
        if any(not r.key for r in resources) or len({r.key for r in resources}) != len(resources):
            raise ValueError('Invalid resource identities')
        result = cls(text(raw['account_key']), text(raw['revision']), plans, tuple(resources),
            count(raw['progress_count']), None if raw['review_count'] is None else count(raw['review_count']),
            None if raw['history_count'] is None else count(raw['history_count']),
            raw.get('empty_account') is True, raw.get('server_confirmed') is True,
            raw.get('trusted_cache') is True, text(raw.get('last_sync', '')),
            text(raw.get('empty_state', '')), tuple(raw.get('supported_scope', [])))
        if not result.account_key or not result.revision:
            raise ValueError('Missing scope or revision')
        if result.empty_state and result.empty_state not in (
                'NONEMPTY', 'TRUE_EMPTY_SUPPORTED_SCOPE', 'PARTIAL_UNSUPPORTED', 'NOT_INITIALIZED'):
            raise ValueError('Unknown server empty authority')
        if result.empty_state == 'TRUE_EMPTY_SUPPORTED_SCOPE' and (plans or resources or result.progress_count):
            raise ValueError('Contradictory supported-scope empty authority')
        if result.empty_account and (plans or resources or result.progress_count or result.review_count or result.history_count):
            raise ValueError('Contradictory empty account')
        return result


class RehydrationState:
    def __init__(self):
        self.account_key = ''
        self.generation = 0
        self.state = None
        self.snapshot = None
        self.deferred = set()

    def authenticated(self, account_key):
        if not isinstance(account_key, str) or not account_key:
            raise ValueError('Authenticated account scope required')
        self.account_key = account_key
        self.generation += 1
        self.state = SyncState.AUTH_SUCCESS
        self.snapshot = None
        self.deferred.clear()
        return self.generation

    def syncing(self):
        if not self.account_key:
            return None
        self.generation += 1  # Each attempt invalidates late responses.
        self.state = SyncState.SYNCING
        self.snapshot = None
        self.deferred.clear()
        return self.generation

    def receive(self, generation, state, payload=None):
        if generation != self.generation or self.state != SyncState.SYNCING:
            return False
        try:
            state = SyncState(state)
            if state not in (SyncState.SYNC_COMPLETE, SyncState.SYNC_OFFLINE, SyncState.SYNC_FAILED, SyncState.NOT_AUTHORIZED):
                return False
            snapshot = CloudView.parse(payload) if payload is not None else None
            if snapshot is not None and snapshot.account_key != self.account_key:
                return False
            if state == SyncState.SYNC_COMPLETE and (snapshot is None or not snapshot.server_confirmed):
                raise ValueError('Completion requires server evidence')
            if state != SyncState.SYNC_COMPLETE and snapshot is not None:
                if not snapshot.trusted_cache or not snapshot.last_sync:
                    snapshot = None
            self.state, self.snapshot = state, snapshot
        except (KeyError, TypeError, ValueError, AttributeError):
            self.state, self.snapshot = SyncState.SYNC_FAILED, None
        return True

    @property
    def workspace_allowed(self):
        return self.snapshot is not None and self.snapshot.empty_state != 'NOT_INITIALIZED' and self.state in (
            SyncState.SYNC_COMPLETE, SyncState.SYNC_OFFLINE, SyncState.SYNC_FAILED)

    @property
    def headline(self):
        if self.state == SyncState.NOT_AUTHORIZED:
            return '登录已失效，请重新登录。'
        if self.state in (SyncState.AUTH_SUCCESS, SyncState.SYNCING):
            return '正在恢复你的学习空间…'
        if self.state == SyncState.SYNC_OFFLINE:
            return '暂时无法连接到云端学习数据。'
        if self.state == SyncState.SYNC_FAILED:
            return '学习空间暂时未能恢复，请重试。'
        if self.state == SyncState.SYNC_COMPLETE:
            if self.snapshot.empty_state == 'TRUE_EMPTY_SUPPORTED_SCOPE':
                return '已恢复的计划、进度和资源范围内暂无学习数据。'
            if self.snapshot.empty_state == 'PARTIAL_UNSUPPORTED':
                return '部分学习数据暂不支持恢复，已支持的数据如下。'
            if self.snapshot.empty_state == 'NOT_INITIALIZED':
                return '学习数据尚未完成初始化，请重试。'
            if self.snapshot.empty_account:
                return '这个账号还没有学习计划。'
            return '你的学习空间已恢复。'
        return '请先登录 MentorFlow。'
