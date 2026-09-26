"""Nonsecret closure journal plus operation-scoped Windows Locker recovery."""
import hashlib
import json

from . import credential_store
from .account_identity import AccountProfile, ProfileMode
from .account_closure_client import STATES, failure, operation_id
from .login_session import account_session_lock

KEY = 'account/closures_v1/'
REPLACEMENT_REFUSED = 'PREPARE_REPLACEMENT_REFUSED'


def can_reprepare(record):
    return bool(record and record['state'] == 'PREPARE_UNKNOWN' and not record['operation_id']
                and not record['submitted'] and not record['local_cleaned'])


class ClosureStore:
    def __init__(self, settings):
        self.settings = settings

    @staticmethod
    def profile(record):
        profile = AccountProfile(ProfileMode.ENTERPRISE_ACCOUNT, record['display_name'], record['issuer_hash'], record['subject_hash'])
        if record['account_id'] != profile.account_id:
            raise failure('CLOSURE_IDENTITY_MISMATCH')
        return profile

    def load(self, profile):
        with account_session_lock(profile):
            self.settings.sync()
            raw = self.settings.value(KEY + profile.account_id, '')
            if not raw:
                return None
            try:
                if not isinstance(raw, str) or len(raw) > 8192:
                    raise ValueError()
                record = json.loads(raw)
                if set(record) != {'version', 'account_id', 'issuer_hash', 'subject_hash', 'display_name', 'api_base_url',
                                   'operation_id', 'state', 'submitted', 'local_cleaned'} or record['version'] != 1:
                    raise ValueError()
                if self.profile(record).account_id != profile.account_id:
                    raise ValueError()
                if record['state'] not in STATES | {'PREPARE_UNKNOWN', REPLACEMENT_REFUSED}:
                    raise ValueError()
                if type(record['submitted']) is not bool or type(record['local_cleaned']) is not bool:
                    raise ValueError()
                if record['local_cleaned'] and record['state'] != 'COMPLETED':
                    raise ValueError()
                if record['operation_id']:
                    operation_id(record['operation_id'])
                elif not (can_reprepare(record) or (record['state'] == REPLACEMENT_REFUSED and record['submitted'])):
                    raise ValueError()
                return record
            except Exception:
                raise failure('CLOSURE_RECOVERY_UNAVAILABLE') from None

    def records(self):
        self.settings.sync()
        result = []
        for key in self.settings.allKeys():
            if key.startswith(KEY):
                raw = json.loads(str(self.settings.value(key)))
                profile = self.profile(raw)
                if key != KEY + profile.account_id:
                    raise failure('CLOSURE_RECOVERY_UNAVAILABLE')
                result.append(self.load(profile))
        return result

    def write(self, record):
        profile = self.profile(record)
        with account_session_lock(profile):
            self.settings.setValue(KEY + profile.account_id, json.dumps(record, ensure_ascii=False, separators=(',', ':')))
            self.settings.sync()
            if self.settings.status().value != 0:
                raise failure('CLOSURE_STORAGE_FAILED')
            if self.load(profile) != record:
                raise failure('CLOSURE_STORAGE_FAILED')

    def begin(self, profile, api_base_url, *, replace_unknown=False):
        with account_session_lock(profile):
            existing = self.load(profile)
            if replace_unknown:
                if not can_reprepare(existing):
                    raise failure('CLOSURE_UNCONFIRMED')
                if existing['api_base_url'] != api_base_url:
                    raise failure('CLOSURE_IDENTITY_MISMATCH')
                # Keep the unknown operation journal. A rejected retry must not
                # erase evidence of an earlier preparation with a lost response.
                self.write(existing)
                return existing
            if existing is not None:
                raise failure('CLOSURE_UNCONFIRMED')
            record = dict(version=1, account_id=profile.account_id, issuer_hash=profile.issuer_hash,
                          subject_hash=profile.subject_hash, display_name=profile.safe_account_label,
                          api_base_url=api_base_url, operation_id='', state='PREPARE_UNKNOWN',
                          submitted=False, local_cleaned=False)
            self.write(record)
            return record

    @staticmethod
    def _namespace(record):
        operation_id(record['operation_id'])
        return dict(issuer_hash=record['issuer_hash'], subject_hash=record['subject_hash'],
                    purpose='closure-' + hashlib.sha256(record['operation_id'].encode()).hexdigest()[:48])

    def save_token(self, record, token):
        try:
            credential_store.store_account_secret(token, **self._namespace(record))
            if credential_store.get_account_secret(**self._namespace(record)) != token:
                raise ValueError()
        except Exception:
            raise failure('CLOSURE_STORAGE_FAILED') from None

    def token(self, record):
        try:
            return credential_store.get_account_secret(**self._namespace(record))
        except Exception:
            raise failure('CLOSURE_RECOVERY_UNAVAILABLE') from None

    def remove(self, record, *, rejected_prepare=False):
        profile = self.profile(record)
        with account_session_lock(profile):
            if rejected_prepare:
                if record['operation_id'] or record['submitted']:
                    raise failure()
            elif record['state'] != 'COMPLETED' or not record['local_cleaned']:
                raise failure()
            else:
                credential_store.delete_account_secret(**self._namespace(record))
            self.settings.remove(KEY + profile.account_id)
            self.settings.sync()
            if self.settings.status().value != 0:
                raise failure('CLOSURE_STORAGE_FAILED')
