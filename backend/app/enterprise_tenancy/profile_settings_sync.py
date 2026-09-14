"""Pure learner-profile/non-secret settings adapter for structured sync."""
from dataclasses import dataclass
from typing import Any, Mapping

NONSECRET_SETTINGS_ALLOWLIST = frozenset({
    'display_name', 'locale', 'timezone', 'theme', 'learning_goal',
    'daily_minutes', 'notifications_enabled', 'default_workspace_view',
})
_SENSITIVE = ('password', 'token', 'secret', 'credential', 'private_key', 'api_key', 'signing_key')

class ProfileSettingsRejected(ValueError):
    pass

def _check_scope(account_id, workspace_id):
    if not isinstance(account_id, str) or not account_id or not isinstance(workspace_id, str) or not workspace_id:
        raise ProfileSettingsRejected('scope_required')

def validate(values: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(values, Mapping): raise ProfileSettingsRejected('mapping_required')
    out = {}
    for key, value in values.items():
        if not isinstance(key, str) or key not in NONSECRET_SETTINGS_ALLOWLIST:
            raise ProfileSettingsRejected('unknown_or_sensitive_field')
        if any(word in key.lower() for word in _SENSITIVE):
            raise ProfileSettingsRejected('sensitive_field')
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise ProfileSettingsRejected('value_not_scalar')
        out[key] = value
    return dict(sorted(out.items()))

@dataclass(frozen=True)
class ProfileSettingsSnapshot:
    account_id: str
    workspace_id: str
    revision: int
    values: dict[str, Any]

    @classmethod
    def create(cls, account_id, workspace_id, revision, values):
        _check_scope(account_id, workspace_id)
        if type(revision) is not int or revision < 0: raise ProfileSettingsRejected('revision_required')
        return cls(account_id, workspace_id, revision, validate(values))

    def apply(self, account_id, workspace_id, current_revision, current_values):
        _check_scope(account_id, workspace_id)
        if account_id != self.account_id or workspace_id != self.workspace_id:
            return current_revision, validate(current_values)
        if type(current_revision) is not int or current_revision > self.revision:
            raise ProfileSettingsRejected('stale_revision')
        merged = validate(current_values); merged.update(self.values)
        return self.revision, validate(merged)
