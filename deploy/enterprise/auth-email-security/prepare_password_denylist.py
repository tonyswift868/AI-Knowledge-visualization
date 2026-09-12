"""Prepare the pinned, public common-password list; never accepts credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent


def prepare(output: Path) -> dict:
    policy = json.loads((ROOT / 'realm-policy.json').read_text(encoding='utf-8'))
    spec = policy['password_denylist']
    with urllib.request.urlopen(spec['source_url'], timeout=30) as response:
        content = response.read(4_000_001)
    if len(content) > 4_000_000 or hashlib.sha256(content).hexdigest() != spec['source_sha256']:
        raise ValueError('Pinned common-password list checksum mismatch')
    if len(content.splitlines()) != spec['source_count']:
        raise ValueError('Pinned common-password list count mismatch')
    output.mkdir(parents=True, exist_ok=True)
    target = output / spec['file_name']
    temporary = target.with_suffix('.tmp')
    temporary.write_bytes(content)
    temporary.replace(target)
    return {'file': target.name, 'sha256': spec['source_sha256'], 'entries': spec['source_count']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'password-blacklists')
    print(json.dumps(prepare(parser.parse_args().output_dir)))
