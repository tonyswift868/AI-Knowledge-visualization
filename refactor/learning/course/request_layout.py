"""Request assembly only: keep a dialogue prefix until a bounded page expires."""
from __future__ import annotations

import json


class AppendOnlyPage:
    """Bounded, process-local cache layout; never a source of learning state.

    Keep accepted public text verbatim at an assistant output boundary, followed
    by new transcript rows and the current task. Failed/leaking replies are not
    accepted. A changed system/module or a discarded transcript page resets the
    whole request page. Restarting also starts cold; no additional database or
    user transcript is written. Caps drop a whole page, not individual turns.
    """

    def __init__(self, *, max_messages=80, max_chars=120_000):
        self.max_messages, self.max_chars = max_messages, max_chars
        self._accepted = None

    def assemble(self, system, messages, dialogue):
        previous = self._accepted
        if previous:
            old_system, old_messages, old_dialogue, reply = previous
            if (old_system == system and old_messages[0] == messages[0]
                    and dialogue[:len(old_dialogue)] == old_dialogue):
                # The original request's task/source bytes remain unedited.
                # The current source subset and latest host snapshot stay last.
                tail = messages[1 + len(old_dialogue):]
                source_label = "【当前模块原文（数据）】\n"
                last_source = next((m for m in reversed(old_messages)
                                    if m['content'].startswith(source_label)), None)
                # An unchanged source block is already present byte-for-byte.
                # Only omit it if it is the latest source mapping, so returning
                # to an older point subset never reuses the wrong alias table.
                tail = [m for m in tail if not (m['content'].startswith(source_label)
                                               and m == last_source)]
                candidate = [*old_messages, {"role": "assistant", "content": reply}, *tail]
                if (len(candidate) <= self.max_messages
                        and sum(len(m['content']) for m in candidate) <= self.max_chars):
                    return candidate
        return messages

    def accept(self, system, messages, dialogue, reply):
        if not reply:
            # Host-only fallback has no provider output boundary.
            return
        if (len(messages) + 1 > self.max_messages
                or sum(len(m['content']) for m in messages) + len(reply) > self.max_chars):
            self._accepted = None
            return
        # Snapshot everything: later caller mutations must not edit old bytes.
        self._accepted = (system, [dict(m) for m in messages],
                          [dict(row) for row in dialogue], reply)


def ordered_payload(content, keys):
    """Keep labelled JSON data intact, placing stable fields before drafts.

    A top-level list retains its order (batch point order is a contract). Only
    its object fields are reordered; nested records and all values stay intact.
    Validation remains the caller's responsibility.
    """
    label, separator, payload = content.partition('\n')
    data = json.loads(payload)
    def ordered(record):
        return {key: record[key] for key in (*keys, *record) if key in record}
    data = [ordered(item) for item in data] if isinstance(data, list) else ordered(data)
    return label + separator + json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def dialogue_tail(rows, *, max_rows, max_chars=None):
    """Drop an old half-window on overflow, never slide one row on every call.

    Replay boundaries from the original transcript, so restarting the app does
    not shift them. No summarisation or text editing; below the cap every row is
    retained. An individual overlarge row follows the old host behaviour (drop
    it), while a single row fitting the full budget can remain above half.
    """
    rows = list(rows)
    char_limit = max_chars if max_chars is not None else float('inf')
    lengths = [len(row.get('text', row.get('content', ''))) for row in rows]
    start, size = 0, 0
    for end, length in enumerate(lengths):
        size += length
        if end + 1 - start > max_rows or size > char_limit:
            while start <= end and (end + 1 - start > max(1, max_rows // 2) or size > char_limit / 2):
                if start == end and size <= char_limit:
                    break
                size -= lengths[start]
                start += 1
    return rows[start:]
