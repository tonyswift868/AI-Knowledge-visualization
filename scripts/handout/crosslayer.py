"""Append source-proven cross-layer links without regenerating the handout."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

from .generate import AUTO_START, AUTO_END, digest, read_json, save_json
from .snapshot import prepare_snapshot, verify_snapshot, git

PAGE = '02-调用关系/跨层对应.md'
README_ID = 'crosslayer-index'
LABELS = dict(bridge='网页桥接/事件', http='HTTP 接口', dispatch='按名字分发', process='跨进程')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def unique(rows):
    return [value for _key, value in sorted({canonical(row): row for row in rows}.items())]


def collect(source: Path):
    from . import crosslayer_bridge, crosslayer_http, crosslayer_dispatch, crosslayer_process
    result = dict(connections=[], unresolved=[], dynamic=[])
    for module in (crosslayer_bridge, crosslayer_http, crosslayer_dispatch, crosslayer_process):
        extracted = module.extract(source)
        for field in result:
            result[field].extend(extracted.get(field, []))
    for field in result:
        result[field] = unique(result[field])
    http_targets = defaultdict(set)
    for row in result['connections']:
        if row['kind'] == 'http':
            http_targets[(row['name'], canonical(row['source']))].add(canonical(row['target']))
    for row in result['connections']:
        declaration = row.get('route_declaration')
        if declaration:
            row['via'] = [*row.get('via', []), declaration]
        if row['kind'] == 'http' and len(http_targets[(row['name'], canonical(row['source']))]) > 1:
            row['rule'] += '；同一路由有多个服务实现，按实际服务选择，不代表同时调用'
    for field in result:
        result[field] = unique(result[field])
    return result


def _endpoint(ep):
    if not ep:
        return '未定'
    return f"{ep['file']}:{ep['line']} `{ep.get('function', '<module>')}`"


def _escape(value):
    return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def _table(header, rows):
    return '\n'.join(['| ' + ' | '.join(header) + ' |',
                       '|' + '|'.join('---' for _ in header) + '|',
                       *('| ' + ' | '.join(_escape(c) for c in row) + ' |' for row in rows)])


def render(result, commit):
    """Stable Markdown + automatic block entries; no timestamps or model text."""
    blocks, parts = [], []
    def auto(ident, body):
        body = body.strip('\n') + '\n'
        blocks.append(dict(file=PAGE, id=ident, sha256=digest(body)))
        parts.append(AUTO_START.format(ident) + '\n' + body + AUTO_END + '\n\n')
    counts = dict(Counter(r['kind'] for r in result['connections']))
    auto('crosslayer-overview', '# 跨层对应\n\n'
         f'源码快照：`{commit}`。本页补充按目录调用页没有表达的连接，按被调文件分组。\n\n'
         '这些是程序按源码中的方法名、注册表、路由、有限分支或打包入口规则配出的关系，'
         '不是 Graphify 的 EXTRACTED calls。每条列明规则；动态值不猜，见最后两节。'
         '源码位置是定义或注册/发起位置，并不表示这些分支每次都会执行。\n\n'
         + '；'.join(f'{LABELS[k]} {counts.get(k, 0)} 条' for k in LABELS)
         + f"；动态分发点 {len(result['dynamic'])}；只找到一头 {len(result['unresolved'])}。\n\n"
         '查某个文件时，同时搜索它在“调用方”“被调方”和“中间证据”三列的路径。'
         '原来的直接调用仍查按目录页和 calls.json。')
    grouped = defaultdict(list)
    for row in result['connections']:
        grouped[row['target']['file']].append(row)
    for path in sorted(grouped):
        rows = []
        for row in sorted(grouped[path], key=lambda r: (r['kind'], r['name'], canonical(r['source']), canonical(r))):
            ident = hashlib.sha256(canonical(row).encode()).hexdigest()[:12]
            rows.append((f'XL-{ident}', LABELS[row['kind']] + '：' + row['name'],
                         _endpoint(row['source']), _endpoint(row['target']),
                         ' → '.join(_endpoint(ep) for ep in row.get('via', [])) or '无', row['rule']))
        auto('target-' + digest(path)[:16], f'## {path}\n\n' + _table(
            ['编号', '连接方式/名字', '调用方文件:行 / 函数', '被调方文件:行 / 函数', '中间证据（按链路）', '确定规则/条件'], rows))
    for field, title in [('dynamic', '动态分发点'), ('unresolved', '只找到一头')]:
        rows = [(LABELS.get(r['kind'], r['kind']) + '：' + r.get('name', ''),
                 _endpoint(r.get('source') or r.get('target')), r['reason']) for r in result[field]]
        auto(field, f'## {title}\n\n' + (_table(['方式/名字', '已知出处', '不能配对的原因'], rows) if rows else '无。'))
    return ''.join(parts), blocks


def _validate(result, source: Path, allowed):
    line_counts = {}
    def endpoint(ep):
        if ep['file'] not in allowed:
            raise ValueError(f"跨层出处不属于讲义快照：{ep['file']}")
        if ep['file'] not in line_counts:
            with (source / ep['file']).open(encoding='utf-8') as f:
                line_counts[ep['file']] = sum(1 for _ in f)
        if not isinstance(ep['line'], int) or not 1 <= ep['line'] <= line_counts[ep['file']]:
            raise ValueError(f'跨层出处行号无效：{ep}')
    for row in result['connections']:
        if row['kind'] not in LABELS or not row.get('rule'):
            raise ValueError('跨层连接缺少类型或推导规则')
        for ep in [row['source'], *row.get('via', []), row['target']]:
            endpoint(ep)
    for key in ('unresolved', 'dynamic'):
        for row in result[key]:
            endpoint(row.get('source') or row['target'])
            if not row.get('reason'):
                raise ValueError('未定连接必须写明原因')


def update_handout(output: Path, source: Path, result, commit):
    """Only this page, README's own block and the manifest may change."""
    manifest = read_json(output / 'manifest.json')
    if manifest['commit'] != commit:
        raise ValueError('跨层源码提交必须等于讲义快照')
    _validate(result, source, {f['path'] for f in manifest['files']})
    body, blocks = render(result, commit)
    readme_path = output / 'README.md'
    with readme_path.open(encoding='utf-8') as f:
        readme = ''.join(f)
    note = ('查某个文件的调用关系时，也请搜索 [跨层对应](02-调用关系/跨层对应.md)。'
            '按目录页保留 Graphify 直接调用，本页补充网页桥接、HTTP、按名字分发与跨进程的源码对应。\n')
    block = AUTO_START.format(README_ID) + '\n' + note + AUTO_END
    pattern = re.compile(re.escape(AUTO_START.format(README_ID)) + r'\n.*?' + re.escape(AUTO_END), re.S)
    if pattern.search(readme):
        readme = pattern.sub(lambda _: block, readme)
    else:
        readme = readme.rstrip('\n') + '\n\n' + block + '\n'
    automatic = [r for r in manifest['automatic'] if r['file'] != PAGE
                 and (r['file'], r['id']) != ('README.md', README_ID)]
    automatic.extend(blocks)
    automatic.append(dict(file='README.md', id=README_ID, sha256=digest(note)))
    manifest['automatic'] = automatic
    counts = {kind: sum(r['kind'] == kind for r in result['connections']) for kind in LABELS}
    manifest['crosslayer'] = dict(schema='handout.crosslayer.v1', commit=commit, page=PAGE,
                                 connections=counts, unresolved=len(result['unresolved']), dynamic=len(result['dynamic']))
    (output / PAGE).write_text(body, encoding='utf-8', newline='\n')
    readme_path.write_text(readme, encoding='utf-8', newline='\n')
    save_json(output / 'manifest.json', manifest)
    return manifest['crosslayer']


def generate_crosslayer(repo, commit, output, work, config):
    metadata = work / 'crosslayer-snapshot.json'
    if metadata.exists():
        saved = read_json(metadata)
        oid = git(repo, 'rev-parse', '--verify', f'{commit}^{{commit}}').decode().strip()
        if oid != saved['commit'] or config.get('exclude', []) != saved['exclude']:
            raise ValueError('工作目录已用于不同提交或排除配置；请换空工作目录')
        source, files = work / 'snapshot', saved['files']
    else:
        oid, source, files, excluded = prepare_snapshot(repo, commit, work, config.get('exclude', []))
        save_json(metadata, dict(commit=oid, files=files, exclude=config.get('exclude', [])))
    verify_snapshot(source, files)
    result = collect(source)
    save_json(work / 'crosslayer-result.json', result)
    return update_handout(output, source, result, oid)
