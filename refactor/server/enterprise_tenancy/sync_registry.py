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
    SyncItemAdapter('plan', 'plans'), SyncItemAdapter('progress', 'progress'),
    SyncItemAdapter('resource', 'resource_metadata'), SyncItemAdapter('task', 'plan_tasks'),
    SyncItemAdapter('calendar', 'calendars'), SyncItemAdapter('review', 'review_fsrs'),
    SyncItemAdapter('learner_profile', 'learner_profile'),
    SyncItemAdapter('learning_run', 'learning_runs'), SyncItemAdapter('history', 'teaching_chat_history'),
    SyncItemAdapter('setting', 'nonsecret_settings'),
)}
SYNC_KINDS = frozenset(SYNC_ITEM_REGISTRY)
SYNC_DOMAINS = tuple(a.domain for a in SYNC_ITEM_REGISTRY.values())
