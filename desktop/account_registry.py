"""Local account selection, never identity or server-deletion authority."""
from contextlib import contextmanager
import json
import threading
import os
from types import SimpleNamespace

from .account_identity import AccountProfile, ProfileMode, identity_hash

KEY = 'account/registry_v1'
_mutex = threading.RLock()
FIELDS = {'account_id','issuer_hash','subject_hash','display_name',
          'remember_session','remember_password','auto_login'}


class AccountRegistry:
    def __init__(self, preferences):
        self.preferences = preferences
        self.settings = preferences.settings

    @contextmanager
    def transaction(self):
        with _mutex:
            # Native Windows QSettings.fileName() is a registry key, not a disk
            # path. Reuse the existing named OS mutex for both native and INI
            # stores; never mkdir or place a lock beside a registry-key string.
            from .login_session import account_session_lock
            location = self.settings.fileName() if hasattr(self.settings,'fileName') else str(id(self.settings))
            anchor = SimpleNamespace(issuer_hash=identity_hash('local-account-registry-v1'),
                                     subject_hash=identity_hash(os.path.normcase(location)))
            with account_session_lock(anchor):
                self.settings.sync()
                yield

    @staticmethod
    def profile(record):
        return AccountProfile(ProfileMode.ENTERPRISE_ACCOUNT, record['display_name'],
                              record['issuer_hash'], record['subject_hash'])

    def _read(self):
        raw = str(self.settings.value(KEY, '') or '')
        if not raw:
            # Legacy selection migrates without inventing password consent.
            profile = self.preferences.load_unvalidated()
            remember, automatic = self.preferences.login_options()
            rows = [] if profile.account_id is None else [self.record(profile,remember,False,automatic)]
            return dict(version=1,current_account_id=profile.account_id,accounts=rows)
        try:
            if len(raw)>65536: raise ValueError()
            def pairs(items):
                value={}
                for key,item in items:
                    if key in value: raise ValueError()
                    value[key]=item
                return value
            value=json.loads(raw,object_pairs_hook=pairs)
            if set(value)!={'version','current_account_id','accounts'} or value['version']!=1: raise ValueError()
            rows=value['accounts']
            if not isinstance(rows,list) or len(rows)>64: raise ValueError()
            ids=set()
            for row in rows:
                if set(row)!=FIELDS: raise ValueError()
                if any(type(row[k]) is not bool for k in ('remember_session','remember_password','auto_login')): raise ValueError()
                if not isinstance(row['display_name'],str) or len(row['display_name'])>320: raise ValueError()
                profile=self.profile(row)
                if row['account_id']!=profile.account_id or profile.account_id in ids: raise ValueError()
                ids.add(profile.account_id)
            if value['current_account_id'] is not None and value['current_account_id'] not in ids: raise ValueError()
            return value
        except (ValueError,TypeError,KeyError,RecursionError):
            raise ValueError('ACCOUNT_REGISTRY_INVALID') from None

    @staticmethod
    def record(profile, remember, password, automatic):
        if profile.account_id is None: raise ValueError('ACCOUNT_REQUIRED')
        return dict(account_id=profile.account_id,issuer_hash=profile.issuer_hash,subject_hash=profile.subject_hash,
            display_name=profile.display_name[:320],remember_session=bool(remember),
            remember_password=bool(password),auto_login=bool(automatic))

    def _write(self,value):
        self.settings.setValue(KEY,json.dumps(value,ensure_ascii=False,separators=(',',':')))
        self.settings.sync()
        if hasattr(self.settings,'status') and self.settings.status().value!=0:
            raise RuntimeError('ACCOUNT_REGISTRY_WRITE_FAILED')

    def snapshot(self):
        with self.transaction(): return self._read()

    def get(self,account_id):
        return next((r for r in self.snapshot()['accounts'] if r['account_id']==account_id),None)

    def add(self,profile,*,remember_session,remember_password,automatic):
        with self.transaction():
            value=self._read();rows=value['accounts']
            rows[:]=[r for r in rows if r['account_id']!=profile.account_id]
            if len(rows)>=64: raise ValueError('ACCOUNT_REGISTRY_FULL')
            rows.append(self.record(profile,remember_session,remember_password,automatic))
            value['current_account_id']=profile.account_id;self._write(value)

    def select(self,account_id):
        with self.transaction():
            value=self._read()
            row=next((r for r in value['accounts'] if r['account_id']==account_id),None)
            if row is None: raise ValueError('ACCOUNT_NOT_REGISTERED')
            value['current_account_id']=account_id;self._write(value)
            self.preferences.save_target(self.profile(row),remember_me=row['remember_session'],auto_login=row['auto_login'])
            return row

    def set_options(self,account_id,**options):
        if set(options)-{'remember_session','remember_password','auto_login'}: raise ValueError('INVALID_OPTIONS')
        with self.transaction():
            value=self._read()
            for row in value['accounts']:
                if row['account_id']==account_id: row.update({k:bool(v) for k,v in options.items()})
            self._write(value)

    def remove(self,account_id):
        with self.transaction():
            value=self._read();value['accounts']=[r for r in value['accounts'] if r['account_id']!=account_id]
            if value['current_account_id']==account_id:
                value['current_account_id']=None;self.preferences.clear()
            self._write(value)
