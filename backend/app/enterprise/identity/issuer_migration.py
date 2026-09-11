"""Closed, time-bounded E109 realm binding; never inferred from a request."""
from dataclasses import dataclass
import json

OLD_ISSUER = 'https://localhost:18080/realms/mentorflow'
NEW_ISSUER = 'https://auth.clannad.win/realms/mentorflow'
AUTHORITY_SHA256 = '5662cb790710dde77eaf1d8d49edd6e4d476426def0c2ded73146d38d8d0d550'


@dataclass(frozen=True)
class IssuerMigration:
    not_before: int
    not_after: int
    authority_sha256: str

    def __post_init__(self):
        if (type(self.not_before) is not int or type(self.not_after) is not int or
                self.not_before <= 0 or not 0 < self.not_after - self.not_before <= 172800 or
                self.authority_sha256 != AUTHORITY_SHA256):
            raise ValueError('Issuer migration requires the exact authority and a window of at most 48 hours')

    def active(self, now: float) -> bool:
        return self.not_before <= now < self.not_after

    @classmethod
    def from_json(cls, raw):
        if raw is None or raw == '':
            return None
        def unique(pairs):
            if len(dict(pairs)) != len(pairs):
                raise ValueError('Duplicate issuer migration fields')
            return dict(pairs)
        value = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != {'not_before', 'not_after', 'authority_sha256'}:
            raise ValueError('Unknown issuer migration fields')
        return cls(**value)


def validate_binding(issuer, identity_issuer, migration):
    if migration is not None and (not isinstance(migration, IssuerMigration) or
            issuer not in (OLD_ISSUER, NEW_ISSUER) or identity_issuer != OLD_ISSUER):
        raise ValueError('Migration is limited to the exact E109 realm and its existing identity namespace')
    if identity_issuer is not None and (identity_issuer != OLD_ISSUER or issuer not in (OLD_ISSUER, NEW_ISSUER)):
        raise ValueError('Unapproved identity issuer alias')


def accepts_issuer(value, primary, migration, now):
    return value == primary or (migration is not None and migration.active(now)
                                and value in (OLD_ISSUER, NEW_ISSUER))
