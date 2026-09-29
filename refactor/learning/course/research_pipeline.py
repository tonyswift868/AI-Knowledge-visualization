"""Validate public search hits before the architect's relevance/scoring stage.

Run from a worker thread. Inject fetcher(url, *, cancel, timeout) ->
FetchedContent for offline tests; it must honour the event-like cancellation
token. The default uses ResearchService's guarded byte fetch. Timeouts/cancel
return promptly and fence late results; Python cannot forcibly kill an injected
synchronous fetcher. One pipeline owns a bounded executor, also across retries.
Use close() (or a with block) when leaving the account/plan.
"""
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
from threading import BoundedSemaphore, Event, Lock, RLock
import time
import unicodedata
from urllib.parse import urlsplit

from .extract import ExtractedDocument, ExtractError, extract_bytes, extract_html
from .source_filter import (RawHit, SourceLists, blocked_keyword_reasons,
                            load_lists, normalize_url, screen)
from refactor.learning.course.research import FetchedContent
from refactor.learning.course.research import ResearchService
from refactor.learning.course.research import ResearchError
from refactor.learning.course.research import ResearchCancelled
from refactor.learning.course.research import ResearchTimeout
from refactor.learning.course.research import ResearchUnsupportedContent


@dataclass(frozen=True)
class VerifiedSource:
    url: str
    final_url: str  # Actual fetched endpoint; normalize only when comparing aliases.
    title: str
    site: str
    tier: str
    category: str
    reasons: tuple[str, ...]
    document: ExtractedDocument
    summary: str
    retrieved_at: str
    content_sha256: str


@dataclass(frozen=True)
class RejectedSource:
    url: str
    title: str
    code: str
    message: str


@dataclass(frozen=True)
class ResearchResult:
    verified: tuple[VerifiedSource, ...]
    rejected: tuple[RejectedSource, ...]
    attempted: int
    elapsed_seconds: float
    cancelled: bool = False


class _Rejected(ResearchError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


class _Cancel:
    def __init__(self, external, closed, timeout):
        self.external, self.closed = external, closed
        self.stopped = Event()
        self.deadline = time.monotonic() + timeout

    def is_set(self):
        return (self.stopped.is_set() or self.closed.is_set()
                or _cancelled(self.external) or time.monotonic() >= self.deadline)

    def check(self):
        if self.closed.is_set() or self.stopped.is_set() or _cancelled(self.external):
            raise _Rejected('cancelled', '资料验证已取消。')
        if time.monotonic() >= self.deadline:
            raise _Rejected('fetch_timeout', '单份资料验证超过时间限制，未采用迟到结果。')


def _cancelled(event):
    return event is not None and event.is_set()


def _urls(values):
    return {url for value in values if (url := normalize_url(value)) is not None}


def _aliases(source):
    return _urls((source.url, source.final_url))


def _raw(value):
    if isinstance(value, RawHit):
        return value
    fields = ('url', 'title', 'snippet')
    parts = [value.get(key, '') if isinstance(value, Mapping) else getattr(value, key, '') for key in fields]
    if any(not isinstance(part, str) for part in parts):
        raise ValueError('搜索结果的 url、title、snippet 必须是字符串。')
    return RawHit(*parts)


_STOP = frozenset('the a an of in on for with and or to from by at is as github readme md pdf main master en www com org html'.split())


def _title_words(title):
    title = unicodedata.normalize('NFKC', title).casefold()
    english = set(re.findall(r'[a-z0-9]{2,}', title)) - _STOP
    chinese = set()
    for phrase in re.findall(r'[\u4e00-\u9fff]+', title):
        chinese.update(phrase[i:i+2] for i in range(len(phrase)-1))
    return english, chinese


def _title_check(search_title, titles):
    """Reject clear lexical disagreement, never claim model-level relevance.

    Translated titles and very short generic titles are left for the scorer,
    with an explicit title_unverified tag. Any matching title/main heading wins.
    """
    titles = [title.strip() for title in titles if title.strip()]
    unavailable = ('404', 'not found', 'access denied', 'just a moment', 'verify you are human',
                   'checking your browser', 'sign in', 'login', '登录', '页面不存在', '访问被拒绝')
    if titles and any(any(word in title.casefold() for word in unavailable) and len(title) < 100 for title in titles[:1]):
        return False, 'title_mismatch'
    wanted = _title_words(search_title)
    comparisons = 0
    for title in titles:
        actual = _title_words(title)
        for left, right in zip(wanted, actual):
            if not left or not right:
                continue
            overlap = len(left & right) / min(len(left), len(right))
            if overlap >= .25:
                return True, ''
            if len(left) >= 2 and len(right) >= 2:
                comparisons += 1
    return (False, 'title_mismatch') if comparisons else (True, 'title_unverified')


class ResearchPipeline:
    def __init__(self, *, fetcher=None, lists_provider=None, concurrency=4, per_site=2,
                 timeout=15.0, max_sources=40, min_text_chars=120, max_bytes=8*1024*1024):
        for name, value in (('concurrency', concurrency), ('per_site', per_site),
                            ('max_sources', max_sources), ('min_text_chars', min_text_chars), ('max_bytes', max_bytes)):
            if type(value) is not int or value < 1:
                raise ValueError(f'{name} 必须是正整数')
        if per_site > 2:
            raise ValueError('同站点并发不得超过 2')
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout 必须是正数')
        self.fetcher, self.lists_provider = fetcher, lists_provider
        self.concurrency, self.per_site, self.timeout = concurrency, per_site, timeout
        self.max_sources, self.min_text_chars, self.max_bytes = max_sources, min_text_chars, max_bytes
        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='research')
        self._closed, self._running, self._site_lock = Event(), Lock(), Lock()
        self._lifecycle = Lock()
        self._sites = {}

    def close(self):
        with self._lifecycle:
            self._closed.set()
            self._executor.shutdown(wait=False, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def _site_slot(self, url, token):
        # A site means a normalized hostname; ports do not create extra slots.
        host = urlsplit(url).hostname
        with self._site_lock:
            semaphore = self._sites.setdefault(host, BoundedSemaphore(self.per_site))
        while not semaphore.acquire(timeout=.02):
            token.check()
        try:
            token.check()
            yield
        finally:
            semaphore.release()

    def _verify(self, hit, token, lists, include_low, excluded):
        def target(url):
            result = screen([RawHit(url)], lists=lists)
            if not result:
                raise _Rejected('invalid_redirect', '最终资料地址无效。')
            checked = result[0]
            if checked.tier == 'blocked':
                raise _Rejected('blocked_redirect', '重定向目标命中屏蔽规则，已停止读取。')
            if checked.tier == 'low' and not include_low:
                raise _Rejected('low_quality', '重定向目标属于低质或转载来源，默认不展示。')
            if checked.url in excluded:
                raise _Rejected(excluded[checked.url], '该资料已经采用或拒绝，不重复展示。')
            return checked

        @contextmanager
        def guard(url, *, cancel=None):
            token.check()
            target(url)
            with self._site_slot(url, token):
                yield

        try:
            token.check()
            if self.fetcher is None:
                fetched = ResearchService(timeout=self.timeout, max_bytes=self.max_bytes).fetch_bytes(
                    {'url': hit.url, 'title': hit.title}, cancel=token, request_guard=guard)
            else:
                with self._site_slot(hit.url, token):
                    fetched = self.fetcher(hit.url, cancel=token, timeout=self.timeout)
            token.check()
            if not isinstance(fetched, FetchedContent) or not isinstance(fetched.body, bytes):
                raise _Rejected('fetch_failed', '抓取器没有返回可验证的原始字节。')
            final = target(fetched.final_url)
            if len(fetched.body) > self.max_bytes:
                raise _Rejected('content_too_large', '资料超过允许的大小，未截断后当作完整资料。')
            media = fetched.content_type.split(';', 1)[0].strip().casefold()
            if media in {'text/html', 'application/xhtml+xml'}:
                doc = extract_html(fetched.body, final.url, cancel=token)
                fallback = urlsplit(final.url).path.rstrip('/').rsplit('/', 1)[-1] or urlsplit(final.url).hostname
            elif media in {'application/pdf', 'text/plain', 'text/markdown'}:
                suffix = {'application/pdf': '.pdf', 'text/plain': '.txt', 'text/markdown': '.md'}[media]
                doc = extract_bytes(fetched.body, 'download' + suffix, max_bytes=self.max_bytes, cancel=token)
                fallback = 'download'
            else:
                raise _Rejected('unsupported_type', '仅接受 HTML、PDF、纯文本或 Markdown 正文。')
            token.check()
            body = '\n\n'.join(block.text for block in doc.blocks)
            bad = blocked_keyword_reasons(doc.title + '\n' + body, lists=lists)
            if bad:
                kind = bad[0]
                label = {'adult': '色情', 'gambling': '赌博', 'piracy': '盗版'}[kind]
                raise _Rejected('content_' + kind, f'资料正文命中{label}关键词，已拒绝展示。')
            if sum(char.isalnum() for char in body) < self.min_text_chars:
                raise _Rejected('body_too_short', f'提取正文少于 {self.min_text_chars} 个有效字符，无法验证资料内容。')
            titles = [doc.title] if doc.title != fallback else []
            titles.extend(block.text for block in doc.blocks if block.kind == 'heading' and len(block.section) <= 1)
            if media in {'text/plain', 'application/pdf'} and not titles:
                first = body.splitlines()[0].strip()
                if 4 <= len(first) <= 180:
                    titles.append(first)
            matches, tag = _title_check(hit.title, titles)
            if not matches:
                raise _Rejected('title_mismatch', '网页标题或主标题与搜索标题明显不符，未作为该资料展示。')
            token.check()
            rank = {'whitelist': 0, 'neutral': 1, 'low': 2}
            tier = max((hit.tier, final.tier), key=rank.__getitem__)
            # A trusted entry redirecting to an unknown host must not keep its trust tier.
            category = final.category if final.url != hit.url else hit.category
            reasons = tuple(dict.fromkeys((*hit.reasons, *final.reasons, *((tag,) if tag else ()))))
            title = (titles[0] if titles else hit.title) or final.url
            return VerifiedSource(hit.url, fetched.final_url, title, urlsplit(final.url).hostname,
                tier, category, reasons, doc, ' '.join(body.split())[:400],
                fetched.retrieved_at or datetime.now(timezone.utc).isoformat(), doc.sha256)
        except _Rejected as exc:
            return RejectedSource(hit.url, hit.title, exc.code, exc.message)
        except (ResearchCancelled, ExtractError) as exc:
            if token.is_set():
                code = 'fetch_timeout' if time.monotonic() >= token.deadline else 'cancelled'
                return RejectedSource(hit.url, hit.title, code, '资料验证超时或已取消，未采用迟到结果。')
            code = getattr(exc, 'code', '')
            if code in {'EMPTY', 'SCANNED_NO_TEXT'}:
                return RejectedSource(hit.url, hit.title, 'body_too_short', str(exc))
            return RejectedSource(hit.url, hit.title, 'extract_failed', '正文提取失败：' + str(exc))
        except ResearchUnsupportedContent:
            return RejectedSource(hit.url, hit.title, 'unsupported_type', '服务器未返回支持的正文类型。')
        except (ResearchTimeout, TimeoutError):
            return RejectedSource(hit.url, hit.title, 'fetch_timeout', '资料抓取超过时间限制。')
        except Exception:
            return RejectedSource(hit.url, hit.title, 'fetch_failed', '资料抓取失败，未把搜索摘要当作已验证正文。')

    def run(self, hits, *, accepted_urls=(), rejected_urls=(), lists: SourceLists | None = None,
            include_low=False, cancel=None) -> ResearchResult:
        """Return input-ordered outcomes; accepted/rejected exclusions apply to redirects too.

        max_sources caps attempted fetches, not the input audit. Each skipped
        item has a reason. Use ResearchPool.take() BEFORE another search round.
        """
        if not self._running.acquire(blocking=False):
            raise RuntimeError('同一验证流水线已有一批任务在运行。')
        try:
            return self._run(hits, accepted_urls, rejected_urls, lists, include_low, cancel)
        finally:
            self._running.release()

    def _run(self, hits, accepted_urls, rejected_urls, lists, include_low, cancel):
        start = time.monotonic()
        lists = lists if lists is not None else (self.lists_provider() if self.lists_provider else load_lists())
        excluded = dict.fromkeys(_urls(accepted_urls), 'already_accepted')
        excluded.update(dict.fromkeys(_urls(rejected_urls), 'already_rejected'))
        raw = [_raw(hit) for hit in hits]
        screened = iter(screen(raw, lists=lists))
        outcomes, todo = {}, []
        for index, item in enumerate(raw):
            if normalize_url(item.url) is None:
                outcomes[index] = RejectedSource(item.url, item.title, 'invalid_url', '搜索结果没有有效的 HTTP(S) 地址。')
                continue
            hit = next(screened)
            reason = None
            if hit.tier == 'blocked':
                reason = ('blocked', '资料地址或搜索标题命中屏蔽规则。')
            elif 'duplicate' in hit.reasons:
                reason = ('duplicate', '该地址与前面的资料重复。')
            elif hit.url in excluded:
                reason = (excluded[hit.url], '该资料已经采用或拒绝，不重复展示。')
            elif 'possible_fork' in hit.reasons:
                reason = ('possible_fork', '该仓库疑似同名 fork，保留原始来源。')
            elif hit.tier == 'low' and not include_low:
                reason = ('low_quality', '该来源属于低质或转载来源，默认不展示。')
            elif len(todo) >= self.max_sources:
                reason = ('limit_reached', '本轮已达到抓取数量上限，该资料尚未验证。')
            if reason:
                outcomes[index] = RejectedSource(hit.url, hit.title, *reason)
            else:
                todo.append((index, hit))
        active, attempted, cancelled = {}, 0, False
        while todo or active:
            if _cancelled(cancel) or self._closed.is_set():
                cancelled = True
                for future, (index, hit, token) in active.items():
                    token.stopped.set()
                    future.cancel()
                    outcomes[index] = RejectedSource(hit.url, hit.title, 'cancelled', '资料验证已取消。')
                for index, hit in todo:
                    outcomes[index] = RejectedSource(hit.url, hit.title, 'cancelled', '资料验证已取消，尚未抓取。')
                break
            # Skip saturated hosts so a slow site cannot occupy every worker.
            sites = {}
            for _, hit, _ in active.values():
                host = urlsplit(hit.url).hostname
                sites[host] = sites.get(host, 0) + 1
            for index, hit in list(todo):
                host = urlsplit(hit.url).hostname
                if len(active) >= self.concurrency:
                    break
                if sites.get(host, 0) >= self.per_site:
                    continue
                token = _Cancel(cancel, self._closed, self.timeout)
                with self._lifecycle:
                    if self._closed.is_set():
                        break
                    future = self._executor.submit(self._verify, hit, token, lists, include_low, excluded)
                active[future] = (index, hit, token)
                todo.remove((index, hit))
                sites[host] = sites.get(host, 0) + 1
                attempted += 1
            if active:
                wait(active, timeout=.02, return_when=FIRST_COMPLETED)
            for future, (index, hit, token) in list(active.items()):
                if _cancelled(cancel) or self._closed.is_set():
                    continue
                if time.monotonic() >= token.deadline:
                    token.stopped.set()
                    future.cancel()
                    outcomes[index] = RejectedSource(hit.url, hit.title, 'fetch_timeout', '单份资料验证超过时间限制。')
                elif future.done():
                    outcomes[index] = future.result()
                else:
                    continue
                del active[future]
        verified, rejected, seen = [], [], set()
        for _, result in sorted(outcomes.items()):
            if isinstance(result, VerifiedSource):
                aliases = _aliases(result)
                if aliases & seen:
                    rejected.append(RejectedSource(result.url, result.title, 'duplicate', '重定向后的资料地址与前面的结果重复。'))
                else:
                    verified.append(result)
                    seen.update(aliases)
            else:
                rejected.append(result)
        return ResearchResult(tuple(verified), tuple(rejected), attempted, time.monotonic()-start, cancelled)


class ResearchPool:
    """Plan-local, in-memory reservoir; selection never happens automatically.

    Keep this instance across search rounds. Add only VerifiedSource objects;
    take() marks them shown, and subsequent takes return the previous round's
    unseen sources before the caller starts another search. It does not score
    relevance or share materials across accounts/plans.
    """
    def __init__(self, *, accepted_urls=(), rejected_urls=()):
        self._accepted, self._rejected = _urls(accepted_urls), _urls(rejected_urls)
        self._records, self._shown = [], set()
        self._lock = RLock()

    @property
    def accepted_urls(self):
        with self._lock:
            return frozenset(self._accepted)

    @property
    def rejected_urls(self):
        with self._lock:
            return frozenset(self._rejected)

    def add(self, sources):
        with self._lock:
            seen = self._accepted | self._rejected | {url for source in self._records for url in _aliases(source)}
            for source in sources:
                if not isinstance(source, VerifiedSource) or source.tier not in {'whitelist', 'neutral', 'low'}:
                    raise ValueError('资料池只接受通过验证的资料。')
                aliases = _aliases(source)
                if aliases & seen:
                    continue
                self._records.append(source)
                seen.update(aliases)

    def take(self, n, *, include_low=False):
        if type(n) is not int or n < 0:
            raise ValueError('n 必须是非负整数')
        with self._lock:
            output = []
            for tier in ('whitelist', 'neutral', 'low') if include_low else ('whitelist', 'neutral'):
                for source in self._records:
                    if len(output) >= n:
                        return tuple(output)
                    if source.tier != tier or _aliases(source) & (self._shown | self._accepted | self._rejected):
                        continue
                    output.append(source)
                    self._shown.update(_aliases(source))
            return tuple(output)

    def _mark(self, url, accepted):
        normalized = normalize_url(url)
        if normalized is None:
            raise ValueError('资料地址无效')
        with self._lock:
            aliases = {normalized}
            for source in self._records:
                if normalized in _aliases(source):
                    aliases.update(_aliases(source))
            target, opposite = (self._accepted, self._rejected) if accepted else (self._rejected, self._accepted)
            target.update(aliases)
            opposite.difference_update(aliases)

    def mark_accepted(self, url):
        self._mark(url, True)

    def mark_rejected(self, url):
        self._mark(url, False)
