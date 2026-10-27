"""Account-local uploaded question sets; literal extraction and resumable tests.

Reuse the material-question compiler/answer units and deterministic grader. This
independent tool does not create plans or add entities to the server sync schema.
"""
from __future__ import annotations

from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import threading
import time
import uuid

from . import material_questions as mq
from .answer_compare import grade, normalize
from .extract import extract_bytes, extract_file
from .point_materials import _json
from .question_bank import grading_question
from ..preferences.storage import account_directory


SYSTEM = '''你只拆用户自己的原题，禁止出题、改写题干/选项/答案、补答案或解题。
逐道找出所有题，包括没有答案的题。问题式标题后面的原文就是其参考答案；普通习题只有明确给出的答案才有答案。
返回 JSON {"questions":[{"stem":"完整原题干","options":["完整原选项"],
"kind":"recall或objective","objective_kind":"choice/multi/fill/calc或null",
"answer":"原答案文字或null","source":{"quote":"本题原文开头唯一片段","end_quote":"本题及答案末尾唯一片段"}}]}。
source 是这道题和它的选项、答案所在的完整原文范围，程序展开并逐字核对。可直接返回完整原文字符串。
stem/option/answer 也可用相同的唯一起止指针；禁止截短题干和答案。
问答题 kind=recall，answer 为完整原参考答案。没有答案的题 answer=null，不能用自己的知识填写。
选择、填空、数值计算是 objective；无法程序比较的解释/证明题是 recall。含多个空时 answer 保留完整原答案。
recognized 是已有程序识别的候选，仅供定位，不要漏掉其他格式的题。不输出要点、评论或代码围栏。'''


def _check(cancel):
    if cancel is not None and cancel.is_set():
        from ..connections.errors import ModelCancelled
        raise ModelCancelled()


def _complete(gateway, packet, system, cancel):
    _check(cancel)
    result = gateway.complete('point_materials', [dict(role='user', content=json.dumps(packet, ensure_ascii=False))],
                              system=system, cancel=cancel, enable_search=False)
    _check(cancel)
    return _json(result.text)


def _points(reference, gateway, cancel):
    _check(cancel)
    return mq.answer_units(reference), None


def compile_row(raw, original, gateway, cancel=None):
    """Fields are literal within THIS question's source span, not another answer."""
    span = mq.literal(raw.get('source'), original)
    stem = mq.literal(raw.get('stem'), span)
    options = [mq.literal(option, span) for option in raw.get('options', [])]
    kind = raw.get('kind')
    if kind not in ('objective', 'recall'):
        raise ValueError('题型未能读取。')
    answer = raw.get('answer')
    row = dict(id=hashlib.sha256((stem+'\n'+span).encode()).hexdigest()[:24], stem=stem,
               kind=kind, options=options, source_text=span, objective_kind=raw.get('objective_kind'))
    if answer is None or answer == '':
        return dict(row, reason='原文没有答案，补上答案后再加入。', missing=True)
    answer = mq.literal(answer, span)
    if kind == 'objective':
        import re
        # Literal A/B/number may also appear in the question/options. Do not
        # mistake that occurrence for a provided answer to an unkeyed exercise.
        marker = mq.ANSWER.search(span)
        explicit = marker or re.search(r'(?im)^(?:#+\s*)?(?:参考)?答案\s*$|["\'](?:answer|答案)["\']\s*:|\|\s*(?:答案|answer)\s*\|', span)
        if not explicit:
            return dict(row, reason='原文未能定位明确的答案字段，补上答案后再加入。', missing=True)
        if marker:
            mq.literal(answer, span[marker.end():])
    candidate = dict(id=row['id'], candidate_type=kind, stem=stem, reference=answer if kind == 'recall' else span,
        answer=answer, options=options, source_title='上传题目', source_url='', source_id='self-test',
        chunk_ids=['self-test'], block_ids=[row['id']], point_ids=[])
    raw = dict(kind=kind, stem=stem, options=options, answer=answer, objective_kind=row['objective_kind'])
    warning = None
    if kind == 'recall':
        points, warning = _points(answer, gateway, cancel)
        raw['key_point_ids'] = [mq.answer_units(answer).index(p)+1 for p in points]
    compiled = mq.compile_question(raw, candidate, 'self-test')
    row.update(answer=compiled['answer'], reference=compiled['reference'], key_points=compiled['key_points'])
    if kind == 'objective':
        try:
            adapted = grading_question(row)
        except ValueError:
            # Uploaded keys also use numeric labels or bare option text. Match
            # original labels/text exactly; never infer semantic equivalence.
            labels = []
            for option in options:
                match = re.match(r'^\s*([A-Za-z]|\d+)[.．、)）:]\s*(.*)$', option, re.S)
                labels.append((match[1], match[2]) if match else ('', option))
            parts = [part.strip() for part in re.split(r'[,，、;；]+', answer) if part.strip()]
            indices = []
            for part in parts:
                matches = [i for i, (label, text) in enumerate(labels) if normalize(part) == normalize(label) or normalize(part) == normalize(text)]
                if len(matches) != 1:
                    raise ValueError('标准答案无法逐字对应资料选项。') from None
                indices.extend(matches)
            if not options or not indices or len(set(indices)) != len(indices):
                raise ValueError('标准答案无法对应资料选项。') from None
            adapted = dict(row, kind='choice' if len(indices) == 1 else 'multi', answer=indices)
        # Several literal fill answers are separated by the source's delimiters.
        if row['objective_kind'] == 'fill':
            import re
            blanks = len(re.findall(r'_{2,}|（\s*）|\(\s*\)|□+', stem))
            if blanks > 1:
                parts = [p.strip() for p in re.split(r'[;；、\n]+', answer) if p.strip()]
                if len(parts) != blanks:
                    raise ValueError('多个空的原答案无法逐项对应，请用分号分开后重新导入。')
                adapted = dict(adapted, kind='fill', answer=[[p] for p in parts])
        row['grading'] = adapted
    if warning:
        row['warning'] = warning
    return row


def extract_questions(document, gateway, cancel=None):
    original = '\n\n'.join(b.text for b in document.blocks)
    if not original.strip():
        raise ValueError('资料没有可读取的文字。')
    # Same recognition as material questions; the model also sees the complete
    # original so user questions are not restricted to classroom heading rules.
    chunks = [dict(id='self-test', source_id='self-test', position=0, text=original,
        block_fragments=[dict(index=i, text=block.text, kind=block.kind, offset=0, section=list(block.section))
                         for i, block in enumerate(document.blocks)])]
    hints = mq.candidates(chunks)
    result = _complete(gateway, dict(text=original, recognized=[dict(stem=c['stem'], kind=c['candidate_type']) for c in hints]), SYSTEM, cancel)
    if not isinstance(result.get('questions'), list):
        raise ValueError('拆题结果未能读取，请重试。')
    questions, missing, rejected, seen = [], [], [], set()
    for raw in result['questions']:
        _check(cancel)
        try:
            if not isinstance(raw, dict):
                raise ValueError('题目字段无法读取。')
            row = compile_row(raw, original, gateway, cancel)
            if row['id'] in seen:
                continue
            seen.add(row['id'])
            (missing if row.get('missing') else questions).append(row)
        except (ValueError, KeyError, TypeError) as exc:
            rejected.append(dict(stem=str(raw.get('stem', '未读取的题目')) if isinstance(raw, dict) else '未读取的题目', reason=str(exc)))
    observed = {mq.plain(q['stem']) for q in questions+missing+rejected}
    for candidate in hints:
        if mq.plain(candidate['stem']) not in observed:
            rejected.append(dict(stem=candidate['stem'], reason='程序识别到原题，但模型未拆出完整字段。请重新导入这一题。'))
    return dict(questions=questions, missing=missing, rejected=rejected, warnings=list(document.warnings))


class SelfTestStore:
    """Small account-local SQLite store, independent of CourseStore and plans."""
    def __init__(self, account_id, data_root, *, clock=time.monotonic, rng=None):
        self.root = account_directory(account_id, data_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'self-tests.sqlite3'
        self.clock, self.rng = clock, rng or random.Random()
        self._active = {}
        self._lock = threading.RLock()
        with closing(self._connect()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS question_sets (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def list(self):
        with self._lock, closing(self._connect()) as db:
            return [self.summary(json.loads(row[0])) for row in db.execute('SELECT body FROM question_sets ORDER BY rowid DESC')]

    @staticmethod
    def summary(body):
        run = body.get('run')
        return dict(id=body['id'], name=body['name'], count=len(body['questions']), missing=len(body['missing']),
                    draft=body['draft'], state='completed' if run and run['completed'] else 'in_progress' if run else 'ready')

    @staticmethod
    def _load(db, key):
        row = db.execute('SELECT body FROM question_sets WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('题集已不存在，请刷新列表。')
        return json.loads(row[0])

    @staticmethod
    def _write(db, body):
        db.execute('INSERT OR REPLACE INTO question_sets VALUES (?,?)', (body['id'], json.dumps(body, ensure_ascii=False)))

    def create(self, name, extracted):
        body = dict(id=uuid.uuid4().hex, name=name.strip() or '我的题集', draft=True, run=None, **extracted)
        with self._lock, closing(self._connect()) as db:
            self._write(db, body)
        return body

    def get(self, key):
        with self._lock, closing(self._connect()) as db:
            return self._load(db, key)

    def update(self, key, action):
        with self._lock, closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                body = self._load(db, key)
                action(body)
                self._write(db, body)
                db.execute('COMMIT')
                return body
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def edit(self, key, *, name=None, remove=None, save=False):
        def change(body):
            if name is not None:
                if not isinstance(name, str) or not name.strip():
                    raise ValueError('请给题集起个名字。')
                body['name'] = name.strip()
            if remove:
                body['questions'] = [q for q in body['questions'] if q['id'] != remove]
                body['missing'] = [q for q in body['missing'] if q['id'] != remove]
                run = body['run']
                if run and remove in run['stats']:
                    run['stats'].pop(remove)
                    run['queue'] = [qid for qid in run['queue'] if qid != remove]
                    run['history'] = [qid for qid in run['history'] if qid != remove]
                    if not run['current'] or run['current']['question_id'] == remove or not any(s['streak'] < run['required'] for s in run['stats'].values()):
                        self._next(body)
            if save:
                body['draft'] = False
        return self.update(key, change)

    def add_answer(self, key, question_id, answer, gateway, cancel=None):
        body = self.get(key)
        if body['run'] and not body['run']['completed']:
            raise ValueError('请先完成这一轮，再补题。')
        row = next((q for q in body['missing'] if q['id'] == question_id), None)
        if row is None or not isinstance(answer, str) or not answer.strip():
            raise ValueError('请填写这道题的原参考答案。')
        span = row['source_text']+'\n答案：'+answer
        compiled = compile_row(dict(row, source=span, answer=answer), span, gateway, cancel)
        _check(cancel)
        def change(current):
            if current['run'] and not current['run']['completed']:
                raise ValueError('自测已开始，请完成后再补题。')
            if not any(q['id'] == question_id for q in current['missing']):
                raise ValueError('这道题已更新，请重新打开。')
            current['missing'] = [q for q in current['missing'] if q['id'] != question_id]
            current['questions'].append(compiled)
            current['run'] = None  # a completed set with a new Q starts a new round
        return self.update(key, change)

    def delete(self, key):
        with self._lock, closing(self._connect()) as db:
            db.execute('DELETE FROM question_sets WHERE id=?', (key,))
            self._active.pop(key, None)

    def _tick(self, body, *, pause=False):
        key, run = body['id'], body.get('run')
        if run and key in self._active:
            now = self.clock()
            run['elapsed_seconds'] += max(0, now-self._active[key])
            self._active[key] = now
        if pause:
            self._active.pop(key, None)

    def start(self, key, required=2, *, restart=False):
        if type(required) is not int or required not in (2, 3):
            raise ValueError('请选择连续答对 2 遍或 3 遍。')
        def change(body):
            if not body['questions']:
                raise ValueError('题集没有带答案的题，请先补上答案。')
            if body['run'] and not restart and not body['run']['completed']:
                return
            queue = [q['id'] for q in body['questions']]
            self.rng.shuffle(queue)
            body['run'] = dict(id=uuid.uuid4().hex, required=required, queue=queue, history=[],
                stats={qid: dict(streak=0, correct=0, wrong=0) for qid in queue},
                current=None, completed=False, elapsed_seconds=0)
            self._next(body)
        body = self.update(key, change)
        self._active[key] = self.clock()
        return self.view(body)

    def resume(self, key):
        with self._lock:
            body = self.get(key)
            if not body.get('run'):
                return self.start(key)
            if not body['run']['completed']:
                self._active[key] = self.clock()
            return self.view(body)

    @staticmethod
    def _next(body):
        run = body['run']
        pending = {qid for qid, stat in run['stats'].items() if stat['streak'] < run['required']}
        if not pending:
            run.update(current=None, completed=True)
            return
        recent = run['history'][-min(2, len(run['queue'])-1):] if len(run['queue']) > 1 else []
        available = [qid for qid in run['queue'] if qid not in recent]
        # If the only unpassed Q is cooling down, revisit another Q to preserve
        # the requested gap. A wrong answer ALWAYS resets that Q's streak.
        qid = next((qid for qid in available if qid in pending), available[0])
        run['current'] = dict(question_id=qid, attempt_id=uuid.uuid4().hex, answer='', revealed=False, verdict=None)

    def reveal(self, key, attempt_id, answer):
        def change(body):
            current, q = self._current(body, attempt_id)
            if q['kind'] != 'recall' or current['verdict'] is not None or not isinstance(answer, str) or not answer.strip():
                raise ValueError('请先写回答，再看参考答案。')
            if not current['revealed']:
                from .question_bank import with_recall_key_points
                points = with_recall_key_points(q)['key_points']
                current.update(answer=answer, revealed=True, key_points=points,
                               ticked=list(range(len(points))))
            self._tick(body)
        return self.view(self.update(key, change))

    @staticmethod
    def _current(body, attempt_id):
        run = body.get('run')
        current = (run or {}).get('current')
        if not current or current['attempt_id'] != attempt_id:
            raise ValueError('这一题已经更新，请重新打开自测。')
        return current, next(q for q in body['questions'] if q['id'] == current['question_id'])

    def submit(self, key, attempt_id, *, answer=None, ticked=None):
        def change(body):
            current, q = self._current(body, attempt_id)
            if current['verdict'] is not None:
                return  # lost response/retry must not count another answer
            missed = []
            if q['kind'] == 'recall':
                if not current['revealed']:
                    raise ValueError('请先写回答并查看参考答案。')
                points = current.get('key_points', q['key_points'])
                if not isinstance(ticked, list) or any(type(i) is not int or not 0 <= i < len(points) for i in ticked):
                    raise ValueError('要点勾选无效。')
                missed = [i for i in range(len(points)) if i not in ticked]
                current['ticked'] = sorted(ticked)
                correct, saved = not missed, current['answer']
            else:
                correct, saved = grade(q['grading'], answer)
            stat = body['run']['stats'][q['id']]
            stat['streak'] = stat['streak']+1 if correct else 0
            stat['correct' if correct else 'wrong'] += 1
            current.update(answer=saved, verdict=dict(correct=correct, missed=missed))
            body['run']['history'].append(q['id'])
            queue = body['run']['queue']
            queue.remove(q['id']); queue.append(q['id'])
            self._tick(body)
        return self.view(self.update(key, change))

    def next(self, key, attempt_id):
        def change(body):
            current, _ = self._current(body, attempt_id)
            if current['verdict'] is None:
                raise ValueError('请先提交当前题。')
            self._tick(body)
            self._next(body)
            if body['run']['completed']:
                self._active.pop(key, None)
        return self.view(self.update(key, change))

    def pause(self, key):
        self.update(key, lambda body: self._tick(body, pause=True))
        return {'ok': True}

    def touch(self, key):
        self.update(key, self._tick)
        return {'ok': True}

    def view(self, body):
        run = copy.deepcopy(body.get('run'))
        if run is None:
            return dict(id=body['id'], name=body['name'], run=None)
        current = run['current']
        question = None
        if current:
            q = next(q for q in body['questions'] if q['id'] == current['question_id'])
            question = dict(id=q['id'], stem=q['stem'], kind=q['kind'], options=q['options'],
                question_type=q.get('grading', {}).get('kind'), blanks=len(q.get('grading', {}).get('answer', [])) if q.get('grading', {}).get('kind') == 'fill' else 0,
                source=dict(title='上传题目', url=None, locator='原题'), origin='found', purpose='self_test',
                revealed=current['revealed'], number=len(run['history'])+(current['verdict'] is None), total=None)
            if current['revealed']:
                from .question_bank import recall_key_point_headings
                points = current.get('key_points', q['key_points'])
                question.update(reference=q['reference'], key_points=points,
                    key_point_headings=recall_key_point_headings(dict(q, key_points=points)),
                    ticked=current.get('ticked', []), answer=current['answer'])
            if current['verdict'] and q['kind'] == 'objective':
                question.update(reference=q['answer'], answer=current['answer'])
        return dict(id=body['id'], name=body['name'], run=run, question=question,
            passed=sum(s['streak'] >= run['required'] for s in run['stats'].values()), total=len(body['questions']),
            results=[dict(id=q['id'], stem=q['stem'], **run['stats'][q['id']]) for q in body['questions']])


def register(bridge, services, window):
    """All work uses the existing bridge pool; only the file picker uses Qt UI."""
    from ..app.bridge import BridgeError
    stores, picked = {}, {}
    lock = threading.Lock()

    def store(current):
        key = (current.account_id, str(current.data_root))
        with lock:
            return stores.setdefault(key, SelfTestStore(*key)) if key not in stores else stores[key]

    def pick(params, ctx):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(window, '选择带答案的题目', '',
            '题目文件 (*.pdf *.docx *.txt *.md *.markdown *.html *.htm *.csv *.json *.epub *.pptx *.xlsx)')
        if not path:
            return {'file': None}
        token = uuid.uuid4().hex
        picked[token] = (services().account_id, path)
        return {'file': dict(token=token, name=Path(path).name)}

    def dispatch(action, params, ctx):
        current = services()
        storage = store(current)
        _check(ctx.cancel)
        key = params.get('set_id')
        if action == 'list':
            return {'sets': storage.list(), 'synced': False}
        if action == 'import':
            token = params.get('file_token')
            if token:
                chosen = picked.get(token)
                if chosen is None or chosen[0] != current.account_id:
                    raise ValueError('请重新选择当前账号要导入的文件。')
                doc = extract_file(chosen[1], cancel=ctx.cancel)
            else:
                text = params.get('text')
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('请选择文件或粘贴题目与答案。')
                doc = extract_bytes(text.encode('utf-8'), '题目.md', cancel=ctx.cancel)
            extracted = extract_questions(doc, current.gateway, ctx.cancel)
            _check(ctx.cancel)
            return {'set': storage.create(params.get('name') or doc.title, extracted)}
        if action == 'get':
            return {'set': storage.get(key)}
        if action == 'edit':
            return {'set': storage.edit(key, name=params.get('name'), remove=params.get('question_id'), save=params.get('save', False))}
        if action == 'add_answer':
            return {'set': storage.add_answer(key, params.get('question_id'), params.get('answer'), current.gateway, ctx.cancel)}
        if action == 'delete':
            storage.delete(key)
            return {'ok': True}
        if action == 'start':
            return storage.start(key, params.get('required', 2), restart=params.get('restart', False))
        if action == 'resume':
            return storage.resume(key)
        if action == 'reveal':
            return storage.reveal(key, params.get('attempt_id'), params.get('answer'))
        if action == 'submit':
            return storage.submit(key, params.get('attempt_id'), answer=params.get('answer'), ticked=params.get('ticked'))
        if action == 'next':
            return storage.next(key, params.get('attempt_id'))
        if action == 'pause':
            return storage.pause(key)
        if action == 'touch':
            return storage.touch(key)
        raise ValueError('未知的自测操作。')

    def handler(action):
        def call(params, ctx):
            try:
                return dispatch(action, params, ctx)
            except ValueError as exc:
                raise BridgeError('SELF_TEST_INVALID', str(exc)) from None
            except (OSError, sqlite3.Error) as exc:
                raise BridgeError('SELF_TEST_STORAGE', '题集读写失败，请重试。') from exc
        return call
    bridge.register('selftest.pick', pick, ui=True)
    for action in ('list', 'import', 'get', 'edit', 'add_answer', 'delete', 'start', 'resume', 'reveal', 'submit', 'next', 'pause', 'touch'):
        bridge.register('selftest.'+action, handler(action))
