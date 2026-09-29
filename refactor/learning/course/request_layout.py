"""Request assembly only: keep a dialogue prefix until a bounded page expires."""
from __future__ import annotations


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
