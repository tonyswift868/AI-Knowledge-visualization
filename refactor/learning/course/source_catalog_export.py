"""Export the seven-field server catalog from the maintained source lists."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import tempfile

FIELDS = ('version', 'updated', 'whitelist', 'github_orgs', 'low', 'blocked', 'blocked_keywords')
DEFAULT_SOURCE = Path(__file__).with_name('data') / 'source_lists.json'


def convert(source):
    if not isinstance(source, dict) or any(k not in source for k in FIELDS):
        raise ValueError('source lists must contain all seven catalog fields')
    if type(source['version']) is not int or source['version'] < 1:
        raise ValueError('version must be a positive integer')
    date.fromisoformat(source['updated'])
    for field in ('whitelist', 'github_orgs', 'low', 'blocked'):
        if not isinstance(source[field], list) or any(not isinstance(r, dict) for r in source[field]):
            raise ValueError(field + ' must be a list of objects')
    keywords = source['blocked_keywords']
    if (not isinstance(keywords, dict) or any(not isinstance(k, str) or not isinstance(v, list)
            or any(not isinstance(word, str) for word in v) for k, v in keywords.items())):
        raise ValueError('blocked_keywords must map categories to lists of words')
    # Explicit projection: candidates (and future editorial fields) never ship.
    data = {key: source[key] for key in FIELDS}
    # The local v2 curriculum/type policy is independent of the existing
    # seven-field server endpoint. Never export a v2 tag without its fields.
    if source['version'] == 2:
        from .source_filter import parse_lists
        parse_lists(source)
        data['version'] = 1
    encoded = (json.dumps(data, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n').encode('utf-8')
    if len(encoded) > 1024 * 1024:
        raise ValueError('catalog exceeds the server 1 MiB limit')
    return encoded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, help='Write atomically; otherwise print JSON to stdout')
    args = parser.parse_args(argv)
    try:
        encoded = convert(json.loads(args.source.read_text(encoding='utf-8-sig')))
        if args.output is None:
            print(encoded.decode('utf-8'), end='')
        else:
            # Preserve a valid old output when parsing or validation fails.
            with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False, suffix='.tmp') as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
            try:
                os.replace(temporary, args.output)
            finally:
                temporary.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f'Catalog export failed: {exc}\n')


if __name__ == '__main__':
    main()
