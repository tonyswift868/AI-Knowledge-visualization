"""Narrow QSettings namespace for analogy history, not a new account store.

The owner is captured once per window. Runtime's validated session registry,
not preferences, usernames or client tenant hints, authorizes each access.
Unmarked legacy settings remain exclusively in explicit Personal Local mode.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .account_identity import AccountProfile, ProfileMode, identity_hash
from .runtime import enterprise_account_server_state


def _scope(window: Any) -> str | None:
    if getattr(window, '_analogy_history_closed', False) or getattr(window, '_account_switch_in_progress', False):
        return None
    profile = getattr(window, 'account_profile', None)
    if not isinstance(profile, AccountProfile):
        return None
    if profile.mode is ProfileMode.PERSONAL_LOCAL:
        if os.getenv('MENTORFLOW_DESKTOP_OIDC_ISSUER') or os.getenv('MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE') == '1':
            return None
        return 'personal-local'
    state = enterprise_account_server_state(profile)
    context = state.get('context')
    if state.get('authenticated') is not True or not isinstance(context, dict):
        return None
    organization, workspace = context.get('organization_id'), context.get('workspace_id')
    if not all(isinstance(value, str) and value.strip() for value in (organization, workspace)):
        return None
    tenant = identity_hash(json.dumps([organization, workspace], separators=(',', ':')))
    return f'account-history/v1/{profile.issuer_hash}/{profile.subject_hash}/{tenant}'


class AnalogySettings:
    def __init__(self, window: Any) -> None:
        self._window = window
        self._settings = window.settings
        self.owner = _scope(window)

    @property
    def active(self) -> bool:
        return self.owner is not None and _scope(self._window) == self.owner

    def _key(self, key: str) -> str | None:
        if not (key.startswith('analogy/') or key == 'news/interests'):
            raise ValueError('not an analogy history setting')
        if not self.active:
            return None
        return key if self.owner == 'personal-local' else f'{self.owner}/{key}'

    def value(self, key: str, default: Any = '') -> Any:
        scoped = self._key(key)
        return default if scoped is None else self._settings.value(scoped, default)

    def setValue(self, key: str, value: Any) -> None:
        scoped = self._key(key)
        if scoped is not None:
            self._settings.setValue(scoped, value)

    def remove(self, key: str) -> None:
        scoped = self._key(key)
        if scoped is not None:
            self._settings.remove(scoped)


def analogy_settings(window: Any) -> AnalogySettings:
    store = getattr(window, '_account_analogy_settings', None)
    if store is None:
        store = AnalogySettings(window)
        window._account_analogy_settings = store
    return store


def analogy_ticket(window: Any) -> tuple[str | None, int]:
    store = analogy_settings(window)
    return (store.owner if store.active else None, int(getattr(window, '_analogy_prefetch_generation', 0)))


def analogy_ticket_current(window: Any, ticket: tuple[str | None, int]) -> bool:
    return ticket[0] is not None and ticket == analogy_ticket(window)
