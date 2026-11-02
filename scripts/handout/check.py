"""Check writing completion and protect the automatically generated blocks."""
from __future__ import annotations

from pathlib import Path
import re

from .generate import DOTS, digest, read_json, sha_file

AUTO = re.compile(r'^<!-- handout:auto id="([^"]+)" -->\n(.*?)^<!-- /handout:auto -->$', re.M | re.S)
TODO = re.compile(r'^<!-- handout:todo id="([^"]+)" -->\n(.*?)^<!-- /handout:todo -->$', re.M | re.S)
ANNOTATION = re.compile(r'^> 【dots 批注｜\d{4}-\d{2}-\d{2}｜[a-fA-F0-9]{7,40}】.*$')
EXAMPLE = '> 【dots 批注｜日期｜提交短号】内容'


def strip_annotations(text):
    return ''.join(line for line in text.splitlines(keepends=True) if not ANNOTATION.fullmatch(line.rstrip('\n')))


def check_handout(out: Path, *, allow_unfilled=False):
    manifest = read_json(out / 'manifest.json')
    errors, unfilled = [], []
    found_auto, found_slots = {}, {}
    for path in sorted(out.rglob('*.md')):
        if '更新' in path.relative_to(out).parts:
            continue
        rel = path.relative_to(out).as_posix()
        with path.open(encoding='utf-8') as f:
            text = ''.join(f)
        for line_no, line in enumerate(text.splitlines(), 1):
            if DOTS in line and not ANNOTATION.fullmatch(line):
                if rel == 'README.md' and line == EXAMPLE:
                    continue
                errors.append(f'{rel}:{line_no} 正文含未按格式标注的 dots 批注开头')
        autos = list(AUTO.finditer(text))
        todos = list(TODO.finditer(text))
        if text.count('<!-- handout:auto ') != len(autos) or text.count('<!-- /handout:auto -->') != len(autos):
            errors.append(f'{rel} 自动块标记不完整')
        if text.count('<!-- handout:todo ') != len(todos) or text.count('<!-- /handout:todo -->') != len(todos):
            errors.append(f'{rel} 待写标记不完整')
        for m in autos:
            key = (rel, m[1])
            if key in found_auto:
                errors.append(f'{rel} 重复自动块 {m[1]}')
            found_auto[key] = digest(strip_annotations(m[2]))
        for m in todos:
            if m[1] in found_slots:
                errors.append(f'重复空位 {m[1]}')
            found_slots[m[1]] = (rel, strip_annotations(m[2]).strip())
    expected_auto = {(row['file'], row['id']): row['sha256'] for row in manifest['automatic']}
    for key, expected in expected_auto.items():
        if key not in found_auto:
            errors.append(f'{key[0]} 缺少自动块 {key[1]}')
        elif expected != found_auto[key]:
            errors.append(f'{key[0]} 自动块 {key[1]} 哈希改变')
    for key in found_auto.keys() - expected_auto.keys():
        errors.append(f'{key[0]} 多余自动块 {key[1]}')
    expected_slots = {s['id']: s for s in manifest['slots']}
    for ident, spec in expected_slots.items():
        actual = found_slots.get(ident)
        if not actual or actual[0] != spec['file']:
            errors.append(f'{spec["file"]} 缺少空位 {ident}')
            unfilled.append(dict(id=ident, file=spec['file'], label=spec['label'], reason='空位标记缺失'))
        elif not actual[1] or re.fullmatch(r'(?:待写|TODO|TBD|待填写|[.。…_\s])*', actual[1], re.I):
            unfilled.append(dict(id=ident, file=spec['file'], label=spec['label']))
    for ident in found_slots.keys() - expected_slots.keys():
        errors.append(f'多余空位 {ident}')
    for row in manifest.get('immutable_files', []):
        path = out / row['file']
        if not path.is_file() or sha_file(path) != row['sha256']:
            errors.append(f'{row["file"]} 自动文件缺失或哈希改变')
    return dict(ok=not errors and (allow_unfilled or not unfilled), errors=errors,
                unfilled=unfilled, slots=len(expected_slots), filled=len(expected_slots) - len(unfilled),
                automatic_blocks=len(expected_auto), allow_unfilled=allow_unfilled)
