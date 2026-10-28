from refactor.desktop.account_identity import AccountPreferenceStore, AccountProfile, ProfileMode
from refactor.desktop.account_registry import AccountRegistry
from refactor.desktop.login_session import LoginSession


class Settings:
    def __init__(self, initial=None, fail_key=None, fail_sync_key=None):
        self.values = dict(initial or {})
        self.persisted = dict(self.values)
        self.fail_key = fail_key
        self.fail_sync_key = fail_sync_key
        self.last_key = None
        self._status = 0

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.last_key = key
        if key == self.fail_key:
            self._status = 1
            return
        self.values[key] = value

    def remove(self, key):
        self.values.pop(key, None)

    def sync(self):
        if self.fail_sync_key is not None and self.last_key == self.fail_sync_key:
            self._status = 1
        if self._status == 0:
            self.persisted = dict(self.values)

    def status(self):
        class Status:
            value = self._status

        return Status()

    def contains(self, key):
        return key in self.values


def profile(subject, name):
    from refactor.desktop.account_identity import identity_hash

    return AccountProfile(ProfileMode.ENTERPRISE_ACCOUNT, name, identity_hash("https://example.test"), identity_hash(subject))


def setup_registry(settings):
    preferences = AccountPreferenceStore(settings)
    registry = AccountRegistry(preferences)
    a, b = profile("a", "A"), profile("b", "B")
    registry.add(a, remember_session=True, remember_password=False, automatic=False)
    registry.add(b, remember_session=True, remember_password=False, automatic=False)
    registry.select(a.account_id)
    return registry, preferences, a, b


def test_first_selection_and_switch_commit_registry_and_preferences_together():
    settings = Settings()
    registry, _, a, b = setup_registry(settings)

    assert registry.snapshot()["current_account_id"] == a.account_id
    assert settings.value("account/subject_hash") == a.subject_hash
    selected = registry.select(b.account_id)
    assert selected["account_id"] == b.account_id
    assert registry.snapshot()["current_account_id"] == b.account_id
    assert settings.value("account/subject_hash") == b.subject_hash


def test_failed_preference_write_raises_and_keeps_previous_selection_recoverable():
    settings = Settings()
    registry, _, a, b = setup_registry(settings)
    settings.fail_key = "account/subject_hash"

    try:
        registry.select(b.account_id)
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed preference sync must not report successful account selection")

    assert registry.snapshot()["current_account_id"] == a.account_id
    assert settings.value("account/subject_hash") == a.subject_hash
    recovered_settings = Settings(initial=settings.persisted)
    recovered_registry = AccountRegistry(AccountPreferenceStore(recovered_settings))
    assert recovered_registry.select(b.account_id)["account_id"] == b.account_id
    assert recovered_registry.snapshot()["current_account_id"] == b.account_id
    assert recovered_settings.value("account/subject_hash") == b.subject_hash


def test_registry_sync_failure_after_value_change_restores_both_sides_and_recovers():
    settings = Settings()
    registry, _, a, b = setup_registry(settings)
    original_registry = settings.value("account/registry_v1")
    settings.fail_sync_key = "account/registry_v1"

    try:
        registry.select(b.account_id)
    except RuntimeError:
        pass
    else:
        raise AssertionError("registry sync failure must be reported")

    assert settings.value("account/registry_v1") == original_registry

    # QSettings status is sticky for this object in the failure model; the next
    # launch constructs a fresh object over the stored values and can retry.
    recovered_state = dict(settings.persisted)
    # Model a backend that persisted the preference sync but rejected registry
    # sync and could not durably apply rollback while status remained in error.
    recovered_state["account/subject_hash"] = b.subject_hash
    recovered_settings = Settings(initial=recovered_state)
    recovered_registry = AccountRegistry(AccountPreferenceStore(recovered_settings))
    assert recovered_registry.snapshot()["current_account_id"] == a.account_id
    assert recovered_settings.value("account/subject_hash") == b.subject_hash
    assert LoginSession(AccountPreferenceStore(recovered_settings)).remembered() == (None, False)
    assert recovered_registry.select(b.account_id)["account_id"] == b.account_id
    assert recovered_settings.value("account/subject_hash") == b.subject_hash


def test_selection_does_not_change_remember_or_auto_login_consent():
    settings = Settings()
    registry, preferences, a, b = setup_registry(settings)
    preferences.save_target(a, remember_me=False, auto_login=False)

    registry.select(b.account_id)

    assert preferences.login_options() == (True, False)
