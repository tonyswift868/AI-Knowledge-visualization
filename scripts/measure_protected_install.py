"""Controlled installation timing; never points at a preserved user release."""
import json
from pathlib import Path
import statistics
from scripts import windows_install_trust as trust


def main():
    root = Path("E:/MentorFlow-TrustTest-20260906/large")
    output = Path("E:/Codex/Temp/E107-FastStart-20260906-03/protected-gate-results.json")
    # Exact payload provenance from the retained release's TEST-ACCESS.md.
    kw = dict(manifest_sha256="a786bd894abea739b3b3f1a8a797b160cc8b56e88cf4f40ce580d8ad78ba9380", source_sha="1531c65116063ec606ed1c623acd29fb68e9e8b7", tree_sha="c441dbfd48fa4bedcac1ee7212866728bc8ac03c")
    trust.protect_installation(root)
    result = {"scope": "source verifier on isolated old payload; not release provenance", "install": trust.verify_installation(root, activate=True, **kw), "repeat": []}
    print(json.dumps(result), flush=True)
    for _ in range(3):
        row = trust.verify_installation(root, **kw)
        result["repeat"].append(row)
        print(json.dumps(row), flush=True)
    values = [row["total_seconds"] for row in result["repeat"]]
    result.update(p50=statistics.median(values), maximum=max(values))
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
