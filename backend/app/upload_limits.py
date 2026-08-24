"""Shared bounded readers for multipart uploads.

``UploadFile.read()`` without a size argument asks Starlette to materialize the
entire request body.  The helpers in this module keep every upload path on a
small, chunked contract and stop after reading at most ``limit + 1`` bytes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


DEFAULT_UPLOAD_CHUNK_BYTES = 1024 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class UploadLimitExceeded(ValueError):
    """Raised as soon as a per-file or aggregate upload budget is exceeded."""

    def __init__(
        self,
        *,
        limit_bytes: int,
        received_bytes: int,
        aggregate: bool = False,
    ) -> None:
        self.limit_bytes = int(limit_bytes)
        self.received_bytes = int(received_bytes)
        self.aggregate = bool(aggregate)
        self.code = "UPLOAD_AGGREGATE_TOO_LARGE" if aggregate else "UPLOAD_TOO_LARGE"
        scope = "aggregate upload" if aggregate else "upload"
        super().__init__(
            f"{scope} exceeds the configured limit of {self.limit_bytes} bytes"
        )

    @property
    def max_bytes(self) -> int:
        return self.limit_bytes

    @property
    def max_size_bytes(self) -> int:
        return self.limit_bytes

    @property
    def details(self) -> dict[str, int | bool]:
        return {
            "max_size_bytes": self.limit_bytes,
            "received_size_bytes": self.received_bytes,
            "aggregate": self.aggregate,
        }


# Keep the shorter name available to callers that describe this as a limit
# error rather than an exceeded-size error.
UploadLimitError = UploadLimitExceeded


def active_upload_root() -> Path:
    """Resolve the configured user-data root used by active/legacy uploads.

    The environment value is checked at call time so tests and desktop launch
    wrappers can configure the root after importing the application module. If
    it is not set, use the module-level default (which tests may safely replace
    with their isolated directory).
    """

    configured = str(os.getenv("MENTORFLOW_ACTIVE_UPLOAD_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # Import lazily to avoid an import cycle: active_teaching owns the
    # compatibility constant and may be monkeypatched by its test fixture.
    from . import active_teaching

    return Path(active_teaching.UPLOAD_ROOT).expanduser().resolve()


async def read_upload_limited(
    upload: Any,
    max_bytes: int,
    *,
    chunk_size: int = DEFAULT_UPLOAD_CHUNK_BYTES,
    aggregate_limit: int | None = None,
    aggregate_used: int = 0,
) -> bytes:
    """Read an upload in bounded chunks and return its bytes.

    At most ``max_bytes + 1`` bytes are retained for one file, and at most
    ``aggregate_limit - aggregate_used + 1`` are read when an aggregate budget
    is supplied.  The extra byte is what makes an over-limit stream fail
    immediately instead of allocating an unbounded request body first. Stream
    exceptions intentionally propagate unchanged to preserve observability.
    """

    max_bytes = int(max_bytes)
    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    chunk_size = int(chunk_size)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    aggregate_used = int(aggregate_used)
    if aggregate_used < 0:
        raise ValueError("aggregate_used must be non-negative")
    if aggregate_limit is not None:
        aggregate_limit = int(aggregate_limit)
        if aggregate_limit < 0:
            raise ValueError("aggregate_limit must be non-negative")
        if aggregate_used > aggregate_limit:
            raise UploadLimitExceeded(
                limit_bytes=aggregate_limit,
                received_bytes=aggregate_used,
                aggregate=True,
            )

    payload = bytearray()
    while True:
        per_file_remaining = max_bytes - len(payload)
        if per_file_remaining < 0:
            raise UploadLimitExceeded(
                limit_bytes=max_bytes,
                received_bytes=len(payload),
            )
        aggregate_remaining = None
        if aggregate_limit is not None:
            aggregate_remaining = aggregate_limit - aggregate_used - len(payload)
            if aggregate_remaining < 0:
                raise UploadLimitExceeded(
                    limit_bytes=aggregate_limit,
                    received_bytes=aggregate_used + len(payload),
                    aggregate=True,
                )

        # A one-byte probe after an exact limit distinguishes an exact-size
        # stream from limit+1 without ever asking the upload object for the
        # complete request body.
        read_size = min(chunk_size, per_file_remaining + 1)
        if aggregate_remaining is not None:
            read_size = min(read_size, aggregate_remaining + 1)
        block = await upload.read(max(1, read_size))
        if not block:
            break
        if not isinstance(block, (bytes, bytearray, memoryview)):
            raise TypeError("upload.read() must return bytes")
        payload.extend(block)
        if len(payload) > max_bytes:
            raise UploadLimitExceeded(
                limit_bytes=max_bytes,
                received_bytes=len(payload),
            )
        if aggregate_limit is not None and aggregate_used + len(payload) > aggregate_limit:
            raise UploadLimitExceeded(
                limit_bytes=aggregate_limit,
                received_bytes=aggregate_used + len(payload),
                aggregate=True,
            )
    return bytes(payload)


# Descriptive aliases for callers/tests that use the bounded-reader wording.
read_bounded_upload = read_upload_limited
read_upload_bytes = read_upload_limited


__all__ = [
    "DEFAULT_UPLOAD_CHUNK_BYTES",
    "UploadLimitExceeded",
    "UploadLimitError",
    "active_upload_root",
    "read_upload_limited",
    "read_bounded_upload",
    "read_upload_bytes",
]
