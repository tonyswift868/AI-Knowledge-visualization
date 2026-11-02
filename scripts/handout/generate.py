"""Assemble editable descriptions around immutable, reproducible metadata."""
from __future__ import annotations

from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from urllib.parse import quote

from .references import build_references
from .snapshot import prepare_snapshot, verify_snapshot, matches

AUTO_START = '<!-- handout:auto id="{}" -->'
AUTO_END = '<!-- /handout:auto -->'
TODO_START = '<!-- handout:todo id="{}" -->'
TODO_END = '<!-- /handout:todo -->'
DOTS = '【dots 批注｜'


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def read_json(path: Path):
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def digest(value: str):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def sha_file(path: Path):
    value = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def page_name(folder: str):
    if folder == '.':
        return '_root.md'
    suffix = '-' + digest(folder)[:8] if '__' in folder or folder == '_root' else ''
    return folder.replace('/', '__') + suffix + '.md'


def folder_of(path: str):
    parent = str(PurePosixPath(path).parent)
    return parent if parent else '.'


def make_groups(files, rules):
    grouped = defaultdict(list)
    tests = rules.get('test_roots', ['tests'])
    for row in files:
        path = row['path']
        p = PurePosixPath(path)
        folder = folder_of(path)
        if any(path == root or path.startswith(root + '/') for root in tests):
            kind, label = 'tests', '测试文件（本目录合并）'
        else:
            category = next((name for name, suffixes in rules.get('categories', {}).items()
                             if p.suffix.casefold() in suffixes), None)
            if category and p.name not in rules.get('single_names', []):
                kind, label = 'resources', category
            else:
                kind, label = 'single', path
        grouped[(folder, kind, label)].append(path)
    result = []
    for number, ((folder, kind, label), paths) in enumerate(sorted(grouped.items()), 1):
        result.append(dict(id=f'G{number:05}', folder=folder, kind=kind,
                           title=label, files=sorted(paths)))
    return result


def source_path(value, source):
    raw = str(value or '').replace('\\', '/')
    prefix = source.as_posix().rstrip('/') + '/'
    if raw.casefold().startswith(prefix.casefold()):
        raw = raw[len(prefix):]
    return raw.removeprefix('./')


def location(value):
    hit = re.search(r'(?:^|:)L?(\d+)', str(value or ''))
    return int(hit[1]) if hit else None


def summarize_graph(graph, source, allowed):
    nodes = {str(n['id']): n for n in graph.get('nodes', [])}
    symbols = defaultdict(list)
    for n in nodes.values():
        path = source_path(n.get('source_file'), source)
        if path in allowed and n.get('_callable'):
            symbols[path].append(dict(name=n.get('label', n['id']),
                                      line=location(n.get('source_location')),
                                      kind='class' if n.get('_callable_class') else 'function'))
    for items in symbols.values():
        items.sort(key=lambda n: (n['line'] or 0, n['name']))
    calls, other = [], []
    rejected = Counter()
    seen = set()
    for e in graph.get('edges', []):
        if e.get('confidence') != 'EXTRACTED':
            rejected[str(e.get('confidence', 'unlabelled'))] += 1
            continue
        a, b = nodes.get(str(e.get('source'))), nodes.get(str(e.get('target')))
        if not a or not b:
            rejected['missing_endpoint'] += 1
            continue
        src = source_path(e.get('source_file') or a.get('source_file'), source)
        dst = source_path(b.get('source_file'), source)
        if src not in allowed or dst not in allowed:
            rejected['outside_scope_or_external'] += 1
            continue
        relation = str(e.get('relation', 'unknown'))
        line = location(e.get('source_location'))
        fallback = line is None
        if fallback:
            line = location(a.get('source_location'))
        row = dict(source_file=src, source_line=line, source_function=a.get('label', a['id']),
                   target_file=dst, target_line=location(b.get('source_location')),
                   target_function=b.get('label', b['id']), relation=relation,
                   confidence='EXTRACTED', line_kind='definition' if fallback else 'callsite',
                   source_node=a['id'], target_node=b['id'])
        key = tuple(str(row[k]) for k in row)
        if key in seen:
            continue
        seen.add(key)
        (calls if relation == 'calls' else other).append(row)
    sort_key = lambda r: (r['source_file'], r['source_line'] or 0, r['target_file'], r['target_function'], r['relation'])
    return dict(symbols=dict(symbols), calls=sorted(calls, key=sort_key),
                other=sorted(other, key=sort_key), rejected=dict(rejected))


class Pages:
    def __init__(self, out):
        self.out = out
        self.autos = []
        self.slots = []

    def auto(self, path, ident, text, *, convention_example=False):
        text = text.strip('\n') + '\n'
        allowed_example = '> ' + DOTS + '日期｜提交短号】内容'
        if any(DOTS in line and not (convention_example and path == 'README.md' and line == allowed_example)
               for line in text.splitlines()):
            raise ValueError('生成正文不能含 dots 批注开头')
        self.autos.append(dict(file=path, id=ident, sha256=digest(text)))
        return AUTO_START.format(ident) + '\n' + text + AUTO_END + '\n\n'

    def slot(self, path, label, **metadata):
        ident = f'S{len(self.slots) + 1:05}'
        self.slots.append(dict(id=ident, file=path, label=label, **metadata))
        return TODO_START.format(ident) + '\n\n' + TODO_END + '\n\n'

    def write(self, relative, text):
        dest = self.out / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8', newline='\n')


def line_count(path):
    try:
        with path.open(encoding='utf-8') as f:
            return sum(1 for _ in f)
    except UnicodeError:
        return 0


def relation_table(rows, limit):
    if not rows:
        return '未解析到直接调用；不代表运行时没有调用。\n'
    lines = [f'共 {len(rows)} 条；显示前 {min(limit, len(rows))} 条，全量见 calls.json。', '',
             '| 调用方文件:行 | 调用方函数 | 被调方文件:定义行 | 被调方函数 | 行号口径 |',
             '|---|---|---|---|---|']
    for r in rows[:limit]:
        lines.append('| ' + ' | '.join(str(v).replace('|', '\\|') for v in
            (f"{r['source_file']}:{r['source_line'] or '?'}", r['source_function'],
             f"{r['target_file']}:{r['target_line'] or '?'}", r['target_function'],
             '调用点' if r['line_kind'] == 'callsite' else '调用方定义行（无调用点）')) + ' |')
    return '\n'.join(lines) + '\n'


def build_handout(source, files, graph, out, config, commit, excluded=(), generated_at=None):
    """Pure assembly after archive/extraction; usable with fixture graph in tests."""
    started = time.monotonic()
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError('输出目录必须为空；旧快照正文不覆盖')
    out.mkdir(parents=True, exist_ok=True)
    pages = Pages(out)
    paths = {r['path'] for r in files}
    by_path = {r['path']: r for r in files}
    groups = make_groups(files, config['groups'])
    folders = {'.'}
    for path in paths:
        parent = PurePosixPath(path).parent
        while str(parent) != '.':
            folders.add(str(parent))
            parent = parent.parent
    summaries = summarize_graph(graph, source, paths)
    refs = build_references(source, sorted(paths), config.get('reference_documents', []), config.get('common_names', []))
    calls = summaries['calls']
    incoming, outgoing = defaultdict(list), defaultdict(list)
    folder_in, folder_out = defaultdict(Counter), defaultdict(Counter)
    for call in calls:
        incoming[call['target_file']].append(call)
        outgoing[call['source_file']].append(call)
        a, b = folder_of(call['source_file']), folder_of(call['target_file'])
        folder_out[a][b] += 1
        folder_in[b][a] += 1
    file_rows = []
    for row in files:
        file_rows.append({**row, 'lines': line_count(source / row['path']),
                          'references': refs[row['path']], 'symbols': summaries['symbols'].get(row['path'], [])})
    by_file = {r['path']: r for r in file_rows}
    for folder in sorted(folders):
        name = page_name(folder)
        rel = '01-文件/' + name
        children = sorted(f for f in folders if f != '.' and folder_of(f) == folder)
        header = f'# {folder}\n\n## 这个文件夹负责什么\n'
        text = pages.auto(rel, 'folder-heading', header)
        text += pages.slot(rel, '文件夹职责', folder=folder, kind='folder')
        child_text = '## 子文件夹\n\n' + ('\n'.join(f'- [{f}]({quote(page_name(f))})' for f in children) or '无。')
        text += pages.auto(rel, 'children', child_text)
        for group in (g for g in groups if g['folder'] == folder):
            text += pages.auto(rel, group['id'] + '-heading', f"## {group['title']}\n\n### 干什么")
            text += pages.slot(rel, '干什么', folder=folder, group=group['id'], kind=group['kind'])
            lines = []
            if group['kind'] != 'single':
                lines += [f"本条合并 {len(group['files'])} 个文件：", '']
            for path in group['files']:
                row = by_file[path]
                lines += [f'**{path}**', '', f"- 文件指纹（git blob）：`{row['blob']}`", f"- 源码/文本行数：{row['lines']}"]
                items = row['symbols']
                lines += ['- 主要类/函数：' + ('；'.join(f"{n['name']}（{n['kind']}，L{n['line'] or '?'}）" for n in items) or '未解析到（可能为空文件、非代码或解析器不支持）')]
                references = row['references']
                lines += ['- 相关编号：' + ('；'.join(dict.fromkeys(f"[{r['id']}]（{r['document']}:{r['line']}）" for r in references)) or '未发现直接文字提及')]
                lines += ['']
            if group['kind'] == 'tests':
                targets = sorted({r['target_file'] for path in group['files'] for r in outgoing[path]
                                  if not r['target_file'].startswith('tests/')})
                # Also use direct parsed imports; this is evidence, not a claim of test coverage.
                targets += sorted({r['target_file'] for r in summaries['other'] if r['source_file'] in group['files']
                                   and r['relation'] == 'imports' and not r['target_file'].startswith('tests/')})
                targets = list(dict.fromkeys(targets))
                lines += ['大概在测哪些正式文件（按直接调用/导入推，非覆盖率结论）：' + ('、'.join(targets) if targets else '待写：没有解析出足够的正式文件关联。')]
            text += pages.auto(rel, group['id'] + '-metadata', '\n'.join(lines))
        pages.write(rel, text)
        rel2 = '02-调用关系/' + name
        counter_text = lambda c: '、'.join(f'{k}（{v} 条）' for k, v in c.most_common()) or '未解析到'
        text = pages.auto(rel2, 'folder-summary', f'# {folder}\n\n'
            f"主要被哪些文件夹调用：{counter_text(folder_in[folder])}\n\n"
            f"主要调用哪些文件夹：{counter_text(folder_out[folder])}\n\n"
            '次数为 Graphify EXTRACTED calls 关系条数；同目录与文件内调用也计数。动态调用、框架路由、跨语言桥不保证覆盖。导入/继承等见 other-relations.json；推断关系已丢弃。')
        text += pages.auto(rel2, 'children', child_text)
        for group in (g for g in groups if g['folder'] == folder):
            for path in group['files']:
                text += pages.auto(rel2, digest(path)[:16], f'## {path}\n\n### 被谁调用\n\n'
                    + relation_table(incoming[path], int(config.get('relation_limit', 30)))
                    + '\n### 调用谁\n\n' + relation_table(outgoing[path], int(config.get('relation_limit', 30))))
        pages.write(rel2, text)
    save_json(out / '02-调用关系/calls.json', dict(commit=commit, calls=calls))
    save_json(out / '02-调用关系/other-relations.json', dict(commit=commit, relations=summaries['other'], rejected=summaries['rejected']))
    annex_files = []
    if config.get('annex', {}).get('enabled'):
        from .annex import generate_annex
        annex_files = generate_annex(source, out / '03-数据和工位', {**config['annex'], 'snapshot_commit': commit})
    else:
        (out / '03-数据和工位').mkdir()
    (out / '更新').mkdir()
    generated_at = generated_at or dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec='seconds')
    readme = pages.auto('README.md', 'introduction', f'# {config.get("project", "项目")} 项目讲义（骨架）\n\n'
        f'快照提交：`{commit}`\n\n生成时间：{generated_at}\n\n'
        '怎么读：先读 01-文件（逐层看文件夹职责和每个文件），再读 02-调用关系。第一步只生成骨架，所有“干什么”空位留给下一批员工读源码填写。\n\n'
        '[第一部分根目录](01-文件/_root.md) · [第二部分根目录](02-调用关系/_root.md)\n\n'
        '附录 03-数据和工位 是生成时的本机结构/工位状态，不属于 Git 历史快照；未打开数据文件内容。\n\n'
        '[数据目录地图](03-数据和工位/data-directory-map.md) · [七工位状态](03-数据和工位/office-status.md)（仅启用附录时有内容）。\n\n'
        '更新办法：先学完第一部分；用户要求更新后，攒一批快照以后的改动追加到“更新”目录，原正文不改。\n\n'
        '自动生成块由固定 handout:auto 注释包围，描述空位由 handout:todo 注释包围。只在空位内填写职责说明，保留标记。目录/文件职责不是模型生成内容。\n\n'
        '调用关系仅 Graphify 静态 EXTRACTED calls；并不保证运行时完整性。全量关系在 JSON；没有边不能解释为没有调用。\n\n'
        f'共 {len(files)} 个文件，{len(folders)} 个文件夹，{len(groups)} 条描述，{len(pages.slots)} 个待写空位。')
    # The only instructional occurrence of the reserved prefix in generated text.
    readme += pages.auto('README.md', 'conventions', '## dots 批注格式\n\n> ' + DOTS + '日期｜提交短号】内容\n\n'
        '实际批注把日期写为 YYYY-MM-DD、提交写为快照短号；使用独立的引用行。批注不计入自动块哈希，也不算填写职责空位。', convention_example=True)
    pages.write('README.md', readme)
    manifest = dict(schema='handout.v1', project=config.get('project'), commit=commit, generated_at=generated_at,
        files=file_rows, folders=[dict(path=f, page=page_name(f)) for f in sorted(folders)],
        groups=groups, slots=pages.slots, automatic=pages.autos,
        immutable_files=[dict(file='02-调用关系/calls.json', sha256=sha_file(out / '02-调用关系/calls.json')),
                         dict(file='02-调用关系/other-relations.json', sha256=sha_file(out / '02-调用关系/other-relations.json'))]
            + [dict(file='03-数据和工位/' + f['path'], sha256=f['sha256']) for f in annex_files],
        excluded=list(excluded), configuration=config, graph_metrics=graph.get('metrics', {}),
        counts=dict(files=len(files), folders=len(folders), single=sum(g['kind'] == 'single' for g in groups),
                    grouped=sum(g['kind'] != 'single' for g in groups), slots=len(pages.slots), calls=len(calls),
                    other_relations=len(summaries['other']), rejected=summaries['rejected']),
        assembly_seconds=round(time.monotonic() - started, 3))
    save_json(out / 'manifest.json', manifest)
    return manifest


def generate(repo, commit, out, work, config, prepared=False):
    started = time.monotonic()
    if out.exists() and any(out.iterdir()):
        raise ValueError('输出目录必须为空；旧快照正文不覆盖')
    if prepared:
        metadata = read_json(work / 'snapshot-metadata.json')
        # Resume only the exact requested commit; never silently scan a workspace.
        from .snapshot import git
        resolved = git(repo, 'rev-parse', f'{commit}^{{commit}}').decode().strip()
        if metadata['commit'] != resolved:
            raise ValueError('预备快照提交不匹配')
        oid, source, files, excluded = resolved, Path(metadata['source']), metadata['files'], metadata['excluded']
        if any(matches(row['path'], p) for row in files for p in config.get('exclude', [])):
            raise ValueError('预备快照范围与当前排除清单不匹配')
        verify_snapshot(source, files)
    else:
        oid, source, files, excluded = prepare_snapshot(repo, commit, work, config.get('exclude', []))
        save_json(work / 'snapshot-metadata.json', dict(commit=oid, source=str(source), files=files, excluded=excluded))
    graph_file = work / 'graph.json'
    if not (prepared and graph_file.exists()):
        python = config.get('graph_python', sys.executable)
        subprocess.run([python, '-m', 'scripts.handout.graph_adapter', '--source', str(source),
                        '--work', str(work / 'graph'), '--out', str(graph_file)], check=True,
                       cwd=Path(__file__).resolve().parents[2])
    manifest = build_handout(source, files, read_json(graph_file), out, config, oid, excluded)
    manifest['generation_seconds'] = round(time.monotonic() - started, 3)
    save_json(out / 'manifest.json', manifest)
    return manifest
