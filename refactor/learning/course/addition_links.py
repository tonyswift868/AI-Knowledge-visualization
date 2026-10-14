"""Import learner-provided URLs into an active plan without a creation draft.

Run in a worker. Each URL commits independently; cancellation rolls back the
current import, keeps earlier successes and fences late fetch results. No model
calls, UI, catalog refresh or plan/ledger edits. Inject the same fetch contracts
as ResearchPipeline and github_repo for offline use.
"""
from __future__ import annotations

from dataclasses import replace
import time

from . import github_repo, materials
from .research_pipeline import ResearchPipeline
from .source_filter import RawHit, load_lists, normalize_url
from refactor.learning.course.research import ResearchCancelled
from refactor.learning.course.research import ResearchTimeout


class LinkImportError(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise LinkImportError('cancelled', '已停止读取；本条未保存，之前成功导入的资料仍保留。')


def _active(store, plan_id, *, creating=False):
    plan = store.get_plan(plan_id)
    if plan['state'] not in (('creating',) if creating else ('active',)) or plan['archived']:
        raise LinkImportError('plan_not_active', '只有进行中且未归档的计划可以中途加资料。')


def _key(url):
    url = normalize_url(url)
    if url is None:
        return None
    repo = github_repo.parse(url)
    if repo:
        owner, name, branch, sub = repo
        url = f'https://github.com/{owner.lower()}/{name.lower()}'
        if branch:
            url += f'/tree/{branch}' + (f'/{sub}' if sub else '')
    return url


def _existing(store, plan_id, aliases):
    for source in store.records('sources', plan_id):
        if source['kind'] != 'url':
            continue
        known = [source.get('url'), source.get('final_url'), *source.get('link_aliases', [])]
        if aliases & {_key(u) for u in known if isinstance(u, str)}:
            return source
    return None


def _use_existing(store, plan_id, source):
    chunks = [c for c in store.records('source_chunks', plan_id) if c['source_id'] == source['id']]
    if not chunks or len(chunks) != source.get('chunk_count', len(chunks)):
        raise LinkImportError('incomplete_source', '这份资料已在计划中，但正文尚未完整同步，请稍后重试。')
    materials.stored_blocks(chunks)  # also checks missing/split block fragments
    if not source['adopted']:
        store.set_source_adopted(plan_id, source['id'], True)
        source = dict(source, adopted=True)
    return source


def _success(store, plan_id, source, status):
    chunks = [c for c in store.records('source_chunks', plan_id) if c['source_id'] == source['id']]
    ids = {c['id'] for c in chunks}
    linked = any(ids.intersection(p['chunk_ids']) for p in store.records('points', plan_id))
    return dict(status=status, source_id=source['id'], title=source['title'],
                url=source['url'], final_url=source.get('final_url', source['url']),
                already_linked=linked, char_count=source.get('char_count', sum(len(c['text']) for c in chunks)),
                chunk_count=len(chunks), warnings=source.get('warnings', []),
                code='already_linked' if linked else '',
                message='资料已关联到现有知识点，不再重复分析。' if linked else
                        '资料已在计划中，可继续分析。' if status == 'existing' else '正文已读取并采用。')


def import_links(store, plan_id, urls, *, fetcher=None, repo_fetch=None, lists=None,
                 cancel=None, max_urls=10, timeout=20.0, creating=False, recommended=False):
    """Return a JSON-ready batch result in input order.

    fetcher(url, *, cancel, timeout) -> FetchedContent; repo_fetch(url, cancel)
    -> bytes. Defaults use public, SSRF-guarded ResearchService requests, never
    login tokens. Caller may supply catalog.load_lists() (no server call here).

    source_ids includes newly imported and existing *unlinked* adopted sources,
    unique and ready for plan_additions.snapshot. Already linked sources have
    their ID in results only. Re-pasting a URL reuses its stored snapshot even if
    the remote changed; this action is not an implicit update of old materials.

    results: original_url, normalized_url, status (imported/existing/failed/
    cancelled), code/message and, on success, source_id/title/url/final_url,
    already_linked, char_count, chunk_count, warnings, elapsed_seconds.
    Invalid batch shape/initial plan raises LinkImportError or StoreError;
    per-URL failures are returned and do not prevent remaining URLs.
    """
    if not isinstance(urls, (list, tuple)) or any(not isinstance(u, str) for u in urls):
        raise LinkImportError('invalid_urls', '请提供网址字符串列表。')
    if type(max_urls) is not int or not 1 <= max_urls <= 100:
        raise LinkImportError('invalid_limit', '一次导入上限须为 1–100。')
    _active(store, plan_id, creating=creating)  # fail before network for a wrong account or plan
    lists = lists if lists is not None else load_lists()
    started, results, source_ids = time.monotonic(), [], []

    def checked_fetch(url, token):
        _check(token)
        body = (repo_fetch or github_repo.default_fetch)(url, token)
        _check(token)
        return body

    with ResearchPipeline(fetcher=fetcher, timeout=timeout, max_sources=1) as pipeline:
        for index, original in enumerate(urls):
            began = time.monotonic()
            url = _key(original)
            result = dict(original_url=original, normalized_url=url)
            try:
                _check(cancel)
                if index >= max_urls:
                    raise LinkImportError('limit_reached', f'一次最多读取 {max_urls} 个网址，本条尚未读取。')
                if url is None:
                    raise LinkImportError('invalid_url', '请输入完整的 HTTP(S) 网址。')
                with store.transaction():
                    _active(store, plan_id, creating=creating)
                    source = _existing(store, plan_id, {url})
                    if source is not None:
                        source = _use_existing(store, plan_id, source)
                        result.update(_success(store, plan_id, source, 'existing'))
                    _check(cancel)
                if source is None:
                    if github_repo.parse(url):
                        verified = github_repo.import_repo(url, fetch=checked_fetch, cancel=cancel, lists=lists)
                        # import_repo's root-only canonical URL drops explicit
                        # branch selection. Keep it here to avoid merging branches.
                        verified = replace(verified, url=url, final_url=url)
                    else:
                        outcome = pipeline.run([RawHit(url)], lists=lists, user_provided=True, cancel=cancel)
                        if not outcome.verified:
                            rejected = outcome.rejected[0]
                            raise LinkImportError(rejected.code, rejected.message)
                        verified = outcome.verified[0]
                    _check(cancel)
                    if recommended:
                        # Keep the same reader (and no alternate-page fallback),
                        # but don't mistake a login/error/video or empty preview
                        # for the recommended lesson. Explicit general sites
                        # remain usable; publisher grading is not an adoption gate.
                        from .source_filter import recommendation_rejection, blocked_keyword_reasons
                        from .source_quality import is_video_source
                        from .source_follow import is_document_preview
                        from .research_pipeline import _title_check, junk_reasons
                        body = '\n\n'.join(b.text for b in verified.document.blocks)
                        reason = recommendation_rejection(verified.final_url,lists=lists)
                        if reason: raise LinkImportError('source_rejected',reason)
                        if is_video_source(verified.final_url,verified.title,body):
                            raise LinkImportError('video_source','这是视频页面，不能直接读取学习正文。')
                        if (not _title_check('',[verified.title])[0] or
                                any(v in body.lower() for v in ('this book has been removed','this page has been removed'))):
                            raise LinkImportError('page_unavailable','资料需要登录、已下架或不可访问。')
                        if is_document_preview(verified.final_url,verified.title,body):
                            raise LinkImportError('document_preview','只能读取预览页，未取得完整正文。')
                        if sum(c.isalnum() for c in body) < pipeline.min_text_chars:
                            raise LinkImportError('body_too_short','读取到的正文太少，不能作为完整学习资料。')
                        if junk_reasons(verified.title,body) or blocked_keyword_reasons(body,lists=lists):
                            raise LinkImportError('source_rejected','正文命中现有资料过滤规则，未采用。')
                    aliases = {url, _key(verified.url), _key(verified.final_url)} - {None}
                    with store.transaction():
                        _active(store, plan_id, creating=creating)  # may have changed while fetching
                        _check(cancel)
                        source = _existing(store, plan_id, aliases)
                        if source is not None:
                            source = _use_existing(store, plan_id, source)
                            status = 'existing'
                        else:
                            try:
                                source = materials.import_verified_source(store, plan_id, verified)
                            except Exception as exc:
                                raise LinkImportError('storage_failed', '正文未能完整入库，请检查磁盘及账号状态后重试。') from exc
                            status = 'imported'
                        known = set(source.get('link_aliases', []))
                        if not aliases <= known:
                            source = dict(source, link_aliases=sorted(known | aliases))
                            store.add_sources(plan_id, [source])
                        result.update(_success(store, plan_id, source, status))
                        _check(cancel)  # rolls back chunks and outbox on late cancellation
                if not result['already_linked'] and result['source_id'] not in source_ids:
                    source_ids.append(result['source_id'])
            except Exception as exc:
                if (cancel is not None and cancel.is_set()) or isinstance(exc, ResearchCancelled):
                    code, message = 'cancelled', '已停止读取；本条未保存，之前成功导入的资料仍保留。'
                elif isinstance(exc, (LinkImportError, github_repo.RepoImportError)):
                    code, message = exc.code, exc.message
                elif isinstance(exc, (ResearchTimeout, TimeoutError)):
                    code, message = 'fetch_timeout', '资料读取超时，请稍后重试。'
                else:
                    code, message = 'import_failed', '资料读取或保存失败，本条未采用，请稍后重试。'
                result = dict(original_url=original, normalized_url=url,
                              status='cancelled' if code == 'cancelled' else 'failed', code=code, message=message)
            result['elapsed_seconds'] = time.monotonic() - began
            results.append(result)
    return dict(source_ids=source_ids, results=results,
                cancelled=any(r['status'] == 'cancelled' for r in results),
                elapsed_seconds=time.monotonic() - started)
