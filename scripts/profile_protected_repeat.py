"""Read-only phase measurements on the fixed isolated installation, not UAT."""
import argparse
import json
from pathlib import Path
import statistics
import time
from unittest.mock import patch

from scripts import windows_install_trust as trust


def measure(root, identities):
    durations = {}

    def timed(name, original):
        def invoke(*args, **kwargs):
            start = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                durations[name] = durations.get(name, 0) + time.perf_counter() - start
        return invoke

    # Wrap, never replace or skip the real authorization and hashing checks.
    with patch.object(trust, '_snapshot', timed('snapshot_inclusive', trust._snapshot)), \
         patch.object(trust, '_check_entry_acls', timed('entry_acl', trust._check_entry_acls)), \
         patch.object(trust, '_root', timed('root_boundary', trust._root)), \
         patch.object(trust, '_identity', timed('inventory_identity', trust._identity)):
        result = trust.verify_installation(root, **identities)
    durations['snapshot_except_entry_acl'] = durations['snapshot_inclusive'] - durations['entry_acl']
    durations['other'] = result['total_seconds'] - sum(durations[k] for k in (
        'snapshot_inclusive', 'root_boundary', 'inventory_identity')) - result['hash_seconds']
    result['phases_seconds'] = durations
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Exclusive output prevents overwriting an earlier timing receipt.
    with args.output.open('x', encoding='utf-8') as output:
        rows = []
        for _ in range(3):
            row = measure(Path('E:/MentorFlow-TrustTest-20260906/large'), dict(
                manifest_sha256='a786bd894abea739b3b3f1a8a797b160cc8b56e88cf4f40ce580d8ad78ba9380',
                source_sha='1531c65116063ec606ed1c623acd29fb68e9e8b7',
                tree_sha='c441dbfd48fa4bedcac1ee7212866728bc8ac03c'))
            rows.append(row)
            print(json.dumps(row), flush=True)
        values = [row['total_seconds'] for row in rows]
        result = dict(scope='source-only read-only diagnostic, not native launch',
                      runs=rows, p50=statistics.median(values), maximum=max(values))
        json.dump(result, output, indent=2)


if __name__ == '__main__':
    main()
