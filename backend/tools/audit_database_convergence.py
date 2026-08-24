"""Run a privacy-safe, read-only convergence audit for the two SQLite stores.

Exit status is deliberately small and script-friendly:

* ``0`` - both stores are valid and converge for mirrored domains;
* ``1`` - both stores are valid but at least one convergence issue exists;
* ``2`` - an input or database-integrity error prevented an audit.

The default JSON omits compared field values.  ``--details`` is an explicit
local diagnostic mode and may expose those values, so it should not be used
for shared logs or support bundles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:  # Running from the repository root (the documented invocation).
    from backend.app.database_convergence import audit_database_convergence
    from backend.app.database_recovery import RecoveryError
except ModuleNotFoundError:  # Running with ``backend`` as the working dir.
    from app.database_convergence import audit_database_convergence
    from app.database_recovery import RecoveryError


EXIT_CONSISTENT = 0
EXIT_INCONSISTENT = 1
EXIT_INPUT_ERROR = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _file_state(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        # Do not carry the caller's absolute path into a default JSON error.
        raise RecoveryError("SQLite source does not exist")
    stat = source.stat()
    return {
        "sha256": _sha256(source),
        "mtime_ns": stat.st_mtime_ns,
        "size_bytes": stat.st_size,
    }


def _read_only_proof(
    before: dict[str, dict[str, Any]],
    paths: dict[str, str | Path],
) -> dict[str, Any]:
    after = {role: _file_state(path) for role, path in paths.items()}
    return {
        "verified": all(before[role] == after[role] for role in before),
        "before": before,
        "after": after,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only audit of legacy app.db and ORM mentorflow_v2.db convergence."
    )
    parser.add_argument("--legacy", required=True, help="Path to the legacy SQLite app.db")
    parser.add_argument("--orm", required=True, help="Path to the ORM SQLite mentorflow_v2.db")
    parser.add_argument(
        "--details",
        action="store_true",
        help=(
            "Include compared legacy_value/orm_value fields (WARNING: explicit local "
            "diagnostic mode; values may contain user content and must not be shared)."
        ),
    )
    return parser


def _error_payload(error: Exception) -> dict[str, Any]:
    raw_message = str(error).casefold()
    if "does not exist" in raw_message:
        message = "SQLite source does not exist"
    elif "invalid sqlite" in raw_message:
        message = "Invalid SQLite source"
    elif isinstance(error, RecoveryError):
        message = "SQLite audit input is invalid"
    else:
        message = "Unable to inspect SQLite sources"
    return {
        "status": "error",
        "error": {
            "type": type(error).__name__,
            "message": message,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = {"legacy": args.legacy, "orm": args.orm}
    try:
        before = {role: _file_state(path) for role, path in paths.items()}
        report = audit_database_convergence(args.legacy, args.orm)
        payload = report.to_dict(details=args.details)
        payload["read_only"] = _read_only_proof(before, paths)
    except (OSError, RecoveryError, ValueError) as error:
        # Error responses intentionally contain no row/field values.  A
        # missing or corrupt input is an operator error, not a divergence.
        print(
            json.dumps(
                _error_payload(error), ensure_ascii=False, sort_keys=True, default=str
            )
        )
        return EXIT_INPUT_ERROR

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    return EXIT_CONSISTENT if report.consistent else EXIT_INCONSISTENT


if __name__ == "__main__":
    sys.exit(main())
