"""Single registry for server-canonical sync item projections."""
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class SyncItemAdapter:
    kind: str
    domain: str
    encode: Callable[[dict], dict] = lambda value: dict(value)

    def project(self, value):
        if not isinstance(value, dict):
            raise ValueError('sync item payload must be an object')
        return self.encode(value)

SYNC_ITEM_REGISTRY = {a.kind: a for a in (
    SyncItemAdapter('plan', 'plans'), SyncItemAdapter('progress', 'progress/mastery'),
    SyncItemAdapter('resource', 'resource metadata'), SyncItemAdapter('task', 'plan tasks'),
    SyncItemAdapter('calendar', 'calendars'), SyncItemAdapter('review', 'review/FSRS'),
    SyncItemAdapter('learner_profile', 'learner/student profile'),
    SyncItemAdapter('learning_run', 'learning runs'), SyncItemAdapter('history', 'teaching/chat history'),
    SyncItemAdapter('setting', 'non-secret workspace/account settings'),
)}
SYNC_KINDS = frozenset(SYNC_ITEM_REGISTRY)
SYNC_DOMAINS = tuple(a.domain for a in SYNC_ITEM_REGISTRY.values())
