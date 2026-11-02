"""Generate: python -m scripts.handout generate --help; check: ... check --help."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .check import check_handout
from .generate import generate, read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    gen = sub.add_parser('generate', help='archive a Git snapshot and assemble the handout')
    gen.add_argument('--repo', type=Path, required=True)
    gen.add_argument('--snapshot', required=True)
    gen.add_argument('--output', type=Path, required=True)
    gen.add_argument('--work', type=Path, required=True, help='empty disposable directory on an allowed drive')
    gen.add_argument('--config', type=Path, default=Path(__file__).with_name('mentorflow.json'))
    gen.add_argument('--exclusions', type=Path, help='optional JSON list replacing config.exclude')
    gen.add_argument('--groups', type=Path, help='optional JSON object replacing config.groups')
    gen.add_argument('--prepared', action='store_true', help='resume an already archived exact snapshot')
    chk = sub.add_parser('check', help='check empty descriptions, metadata integrity and dots format')
    chk.add_argument('--output', type=Path, required=True)
    chk.add_argument('--allow-unfilled', action='store_true', help='first-stage skeleton integrity only')
    cross = sub.add_parser('crosslayer', help='append source-proven cross-layer links to an existing snapshot handout')
    cross.add_argument('--repo', type=Path, required=True)
    cross.add_argument('--snapshot', required=True)
    cross.add_argument('--output', type=Path, required=True)
    cross.add_argument('--work', type=Path, required=True, help='empty disposable archive directory')
    cross.add_argument('--config', type=Path, default=Path(__file__).with_name('mentorflow.json'))
    args = parser.parse_args(argv)
    if args.command == 'crosslayer':
        from .crosslayer import generate_crosslayer
        result = generate_crosslayer(args.repo.resolve(), args.snapshot, args.output.resolve(),
                                     args.work.resolve(), read_json(args.config))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == 'generate':
        config = read_json(args.config)
        if args.exclusions:
            config['exclude'] = read_json(args.exclusions)
        if args.groups:
            config['groups'] = read_json(args.groups)
        result = generate(args.repo.resolve(), args.snapshot, args.output.resolve(), args.work.resolve(), config, args.prepared)
        print(json.dumps(dict(commit=result['commit'], counts=result['counts'],
                              seconds=result['generation_seconds']), ensure_ascii=False))
        return 0
    result = check_handout(args.output.resolve(), allow_unfilled=args.allow_unfilled)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
