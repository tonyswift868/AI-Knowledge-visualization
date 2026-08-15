from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.credential_store import CredentialStoreError, store_secret  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Store a Provider key in the Windows credential backend and print "
            "only its opaque MentorFlow reference."
        )
    )
    parser.add_argument(
        "--replace",
        metavar="CREDENTIAL_REF",
        help="replace the secret behind an existing MentorFlow credential reference",
    )
    args = parser.parse_args()
    secret = getpass.getpass("Provider API key (hidden input): ")
    if not secret:
        print("Credential was not stored: empty input.", file=sys.stderr)
        return 2
    try:
        credential_ref = store_secret(secret, args.replace)
    except CredentialStoreError as exc:
        print(f"Credential was not stored: {exc.code}: {exc.message}", file=sys.stderr)
        return 1
    finally:
        secret = ""
    print(credential_ref)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
