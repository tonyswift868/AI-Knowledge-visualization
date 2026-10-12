"""Reduce read-only before/after inventories to inspectable report evidence."""
from pathlib import Path
import hashlib
import json

TASK = Path('D:/MentorFlow/.local/test-tmp/dir105')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def main():
    before, after = [json.loads((TASK/name).read_text(encoding='utf-8-sig')) for name in ('before.json','after.json')]
    def registry(snapshot):
        return sorted((r for r in snapshot['registry'] if '.UAT.' not in r['key']), key=lambda r:(r['view'],r['key']))
    def links(snapshot):
        return sorted(snapshot['shortcuts'], key=lambda r:r['path'])
    def files(snapshot):
        return {r['path']:r for r in snapshot['formalFiles']}
    a,b = files(before),files(after)
    delta = [dict(path=k,before=a.get(k),after=b.get(k)) for k in sorted(a.keys()|b.keys()) if a.get(k)!=b.get(k)]
    result = dict(formal_registry_unchanged=registry(before)==registry(after),
        formal_registry_before=registry(before), formal_registry_after=registry(after),
        shortcuts_unchanged=links(before)==links(after), shortcut_count=len(links(before)),
        shortcut_sha_before=digest(links(before)), shortcut_sha_after=digest(links(after)),
        formal_file_count_before=len(a), formal_file_count_after=len(b), formal_file_changes=delta,
        formal_tree_sha_before=digest(a),formal_tree_sha_after=digest(b),
        default_path=after['defaultPath'],default_before=before['defaultFiles'],default_after=after['defaultFiles'],
        preview_registry_remaining=[r for r in after['registry'] if 'dir105' in r['key']],
        preview_shortcuts_remaining=[r for r in after['shortcuts'] if 'dir105' in r['path'] or 'dir105' in r['target']])
    (TASK/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('formal_registry_before','formal_registry_after','formal_file_changes')},ensure_ascii=False,indent=2))
    print('formal file deltas:',len(delta))
    print(json.dumps([r['path'] for r in delta],ensure_ascii=False))


if __name__ == '__main__':
    main()
