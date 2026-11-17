#!/usr/bin/env python3
"""Prepare a candidate nginx config that redirects only the existing /download route.

This script never edits its input, reloads nginx, or connects to a server. Run it
on a reviewed copy of the active vhost after the server change is authorized.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

def prepare(source: Path, destination: Path, from_url: str, to_url: str) -> None:
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError('candidate output must not overwrite the source config')
    text = source.read_text(encoding='utf-8')
    blocks = list(re.finditer(r'(?m)^[ \t]*location\s*=\s*/download\s*\{[^{}]*\}', text))
    if len(blocks) != 1:
        raise ValueError(f'expected exactly one exact-match /download location; found {len(blocks)}')
    block = blocks[0]
    contents = block.group(0)
    if 'proxy_pass' in contents:
        raise ValueError('refusing to rewrite a proxied /download location')
    redirects = list(re.finditer(r'\breturn\s+30[1278]\s+(?P<target>[^;\s]+)\s*;', contents))
    if len(redirects) != 1 or redirects[0].group('target').strip('\"\'') != from_url:
        raise ValueError('expected the current /download redirect to equal --from exactly once')
    # Replace the whole URL, not its suffix inside an existing absolute URL.
    redirect = redirects[0]
    replaced = contents[:redirect.start('target')] + to_url + contents[redirect.end('target'):]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text[:block.start()] + replaced + text[block.end():], encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='reviewed copy of the active nginx vhost')
    parser.add_argument('--output', type=Path, required=True, help='candidate file; must differ from --config')
    parser.add_argument('--from', dest='from_url', required=True, help='current exact redirect target')
    parser.add_argument('--to', dest='to_url', required=True, help='replacement redirect target')
    args = parser.parse_args()
    prepare(args.config, args.output, args.from_url, args.to_url)
    print(f'Prepared candidate only: {args.output}')
    print('Review diff and run nginx -t against the candidate before an authorized deployment.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
