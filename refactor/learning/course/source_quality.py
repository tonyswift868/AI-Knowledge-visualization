"""Source-type admission. Publisher classification needs visible original evidence.

Scores use the fetched final URL and body, never a search snippet. Unknown
publishers remain below the automatic admission threshold. User materials are
retained with this same assessment, clearly distinguished from recommended ones.
"""
from datetime import date
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit, unquote

from .source_filter import RawHit, screen, load_lists, github_repo, normalize_url, classify_source

MIN_CREDIBILITY = 4
MIN_RELEVANCE = 4
MIN_SOURCES = 1  # Search display only. Readiness is point_sources coverage, not source count.
MAX_SEARCH_ROUNDS = 3
_STOP = set('learn learning study understand explain introduction basics basic tutorial course guide official documentation '
            'the and for with from about into that this how what beginner advanced intermediate'.split())
_COMMUNITY = re.compile(r'(^|[./_-])(forum|forums|community|discuss|discussion|discussions|issues|pull|pulls|wiki|blog|blogs)(/|[._-]|$)', re.I)
_PERSONAL = re.compile(r'(~|(^|[./])(people|users|student|students|personal|sites)\.)|/(~|users?/|students?/|personal/)', re.I)
_REPOST = re.compile(r'转载|转自[：:]|原文链接[：:]|译自[：:]|reprinted\s+(?:from|with)|originally published (?:at|on)|mirror of', re.I)
_SCHEDULE_DATE = re.compile(r'20\d\d\s*[-/.年]\s*\d{1,2}\s*(?:[-/.月]\s*\d{1,2}\s*日?|月)')
_AGGREGATORS = {'arxiv.org', 'openreview.net', 'zenodo.org', 'semanticscholar.org', 'openaire.eu',
                'pubmed.ncbi.nlm.nih.gov', 'doi.org', 'crossref.org'}
_GENERIC_CN = {'学习', '入门', '基础', '理解', '掌握', '教程', '课程', '如何', '官方', '相关', '资料', '概念', '应用', '熟练'}
_MONTHS = {name: index for index, name in enumerate(('january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december'), 1)}
_CONCEPTS = {
    'functions': ('function', '函数', '関数', 'funktion', 'funções', 'função'),
    'parameters': ('parameter', '参数', 'パラメーター', 'parametern', 'parâmetros', 'parâmetro'),
    'hypertension': ('high blood pressure', '高血压'),
    'eigenvalues': ('eigenvalue', '特征值'),
    'eigenvectors': ('eigenvector', '特征向量'),
    'tang': ('唐朝', '唐代'),
    'silk road': ('silk roads', '丝绸之路'),
    'present perfect': ('现在完成时',),
    'cuneiform': ('楔形文字',),
    'silk': ('丝绸之路', 'routes de la soie', '실크로드'),
    'road': ('丝绸之路', 'routes de la soie', '실크로드'),
    'pressure': ('压强',),
    'list': ('lists', '列表'),
}


def _host_is(host, suffix):
    return host == suffix or host.endswith('.' + suffix)


def content_identity(url):
    """Group known language/version mirrors of one publisher's lesson.

    Only publisher-controlled, observed URL shapes are collapsed. Different
    lessons on the same site must remain separate even when titles match.
    """
    if not isinstance(url, str) or not url:
        return ''
    normalized = normalize_url(url) or url
    parsed = urlsplit(normalized)
    host, path = parsed.hostname or '', parsed.path.rstrip('/')
    if host == 'learn.microsoft.com':
        path = re.sub(r'^/[a-z]{2}(?:-[a-z]{2})?(?=/training/modules/)', '', path, flags=re.I)
        return 'https://learn.microsoft.com' + path
    if host == 'docs.python.org':
        path = re.sub(r'^/(?:zh-cn|ja|fr|de|pt-br)/(?=3/)', '/', path, flags=re.I)
        return 'https://docs.python.org' + path
    if host == 'github.com':
        source = re.fullmatch(r'/python/cpython/blob/[0-9a-f]{7,40}/Doc/(tutorial|reference)/([^/]+)\.rst', path, re.I)
        if source:
            return f'https://docs.python.org/3/{source[1].lower()}/{source[2]}.html'
    if host == 'modelcontextprotocol.io' and path.startswith('/specification/'):
        path = re.sub(r'^/specification/20\d\d-\d\d-\d\d', '/specification/current', path)
        path = re.sub(r'/index(?:\.md)?$', '', path)
        path = re.sub(r'\.md$', '', path)
        return 'https://modelcontextprotocol.io' + path
    if host == 'takeielts.britishcouncil.org':
        return 'https://' + host + path
    return normalized


def terms(text):
    text = text.casefold()
    words = set(re.findall(r'[a-z][a-z0-9+#.-]{2,}', text)) - _STOP
    for phrase in re.findall(r'[\u4e00-\u9fff]+', text):
        words.update(phrase[i:i+2] for i in range(len(phrase)-1) if phrase[i:i+2] not in _GENERIC_CN)
    return words


def _occurrences(text, term):
    variants = (term, *_CONCEPTS.get(term, ()))
    total = 0
    for variant in variants:
        pattern = (r'(?<![a-z0-9])' + re.escape(variant) + r'(?![a-z0-9])'
                   if re.search(r'[a-z]', variant) else re.escape(variant))
        total += len(re.findall(pattern, text, re.I))
    return total


class _Dates(HTMLParser):
    def __init__(self):
        super().__init__(); self.values = []; self._date_depth = 0; self._script = False; self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        key = (attrs.get('property') or attrs.get('name') or attrs.get('itemprop') or '').casefold()
        if tag == 'meta' and key in {'article:published_time', 'article:modified_time', 'date', 'dc.date', 'datemodified', 'datepublished'}:
            self.values.append(attrs.get('content', ''))
        if tag == 'time' and attrs.get('datetime'):
            self.values.append(attrs['datetime'])
        label = ' '.join((attrs.get('class', ''), attrs.get('id', ''), attrs.get('itemprop', ''))).casefold()
        if tag in {'time', 'span', 'div'} and re.search(r'(^|[\s_-])(timestamp|publish(?:ed)?|last.updated|date.modified)([\s_-]|$)', label):
            self._date_depth += 1
        if tag == 'script':
            self._script = True

    def handle_data(self, data):
        if self._date_depth:
            self.values.append(data.strip())
        if self._script:
            self.scripts.append(data)

    def handle_endtag(self, tag):
        if tag in {'time', 'span', 'div'} and self._date_depth:
            self._date_depth -= 1
        if tag == 'script':
            self._script = False


def _date_value(raw):
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        pass
    match = re.search(r'\b(\d{1,2})\s+([A-Za-z]+)\s+(20\d\d)\b', raw)
    if match and match[2].casefold() in _MONTHS:
        try: return date(int(match[3]), _MONTHS[match[2].casefold()], int(match[1]))
        except ValueError: return None
    match = re.search(r'\b([A-Za-z]+)\s+(\d{1,2}),?\s+(20\d\d)\b', raw)
    if match and match[1].casefold() in _MONTHS:
        try: return date(int(match[3]), _MONTHS[match[1].casefold()], int(match[2]))
        except ValueError: return None
    match = re.search(r'\b(20\d\d)年(\d{1,2})月(\d{1,2})日', raw)
    if match:
        try: return date(*map(int, match.groups()))
        except ValueError: return None
    return None


def _structured_dates(value):
    if isinstance(value, list):
        for item in value:
            yield from _structured_dates(item)
    elif isinstance(value, dict):
        kind = value.get('@type', '')
        if isinstance(kind, str) and kind in {'Article', 'NewsArticle', 'MedicalWebPage', 'WebPage', 'TechArticle'}:
            for key in ('dateModified', 'datePublished'):
                if key in value:
                    yield value[key]
        for item in value.get('@graph', []):
            yield from _structured_dates(item)


def published_date(html):
    """Only explicit publication/update metadata, not fetch time/copyright years."""
    parser = _Dates()
    parser.feed(html.decode('utf-8', errors='replace') if isinstance(html, bytes) else html)
    visible = [value for raw in parser.values if (value := _date_value(raw))]
    structured = []
    for script in parser.scripts:
        try:
            structured.extend(value for raw in _structured_dates(json.loads(script)) if (value := _date_value(raw)))
        except (ValueError, TypeError):
            # Some institutional sites assign schema.org Article JSON to a JS
            # variable rather than using ld+json. Parse only an Article block.
            if 'schema.org' in script and re.search(r'"@type"\s*:\s*"(?:Article|MedicalWebPage)"', script):
                structured.extend(value for raw in re.findall(r'"date(?:Published|Modified)"\s*:\s*"([^"]+)"', script)
                                  if (value := _date_value(raw)))
    # Institution pages often list newer, unrelated stories after the article.
    # Prefer the page's Article object; otherwise the first explicit page date.
    return (max(structured).isoformat() if structured else visible[0].isoformat() if visible else None)


def body_publication_date(body):
    """Use only an explicit date label near the start of the fetched body."""
    for line in body.splitlines()[:12]:
        match = re.search(r'\b(?:published|last updated|updated on|更新于|发布日期)\s*[:：]?\s*(.{8,36})', line, re.I)
        if match and (parsed := _date_value(match[1])):
            return parsed.isoformat()
        match = re.search(r'\b(?:DALLAS|WASHINGTON)\s*,\s*(.{8,26})\s+[—–-]', line)
        if match and (parsed := _date_value(match[1])):
            return parsed.isoformat()
    return None


def is_landing_page(title, body):
    return (bool(re.search(r'\.pdf(?:\s*\||$)', title, re.I) and re.search(r'Download File|下载文件', body, re.I))
            or bool(re.search(r'lesson plans?\b', title, re.I) and len(re.findall(r'View lesson|查看课程', body, re.I)) >= 2)
            or bool(re.search(r'Your browser does not support.*?play this video', body, re.I | re.S)
                    and not re.search(r'(^|\n)\s*(?:Transcript|文字稿|字幕全文)\s*(\n|$)', body, re.I)))


def _assess(url, title, body, *, goal, level, modules, today, lists, reasons=(), published_at=None, warnings=(), headings=(), publisher_evidence=None):
    lists = lists or load_lists()
    today = today or date.today()
    checked = screen([RawHit(url or '', title)], lists=lists)
    hit = checked[0] if checked else None
    parsed = urlsplit(url or '')
    host, path = parsed.hostname or '', unquote(parsed.path)
    rules = [r for r in lists.whitelist if _host_is(host, r.match)]
    rule = max(rules, key=lambda r: len(r.match)) if rules else None
    credibility, publisher, category, codes = 2, '来源身份未核实', 'unknown', []
    if rule:
        publisher, category = rule.brand, rule.category
        credibility = 5 if category in {'official_docs', 'standards'} else 4
        codes.append('listed_publisher')
    identity = classify_source(url or '', lists=lists, page_text=body, publisher_evidence=publisher_evidence)
    # Legacy server whitelist entries do not override the source-type policy.
    credibility = 5 if identity['level'] == 1 else 4 if identity['level'] == 2 else 2
    publisher, category = identity['publisher'] or '来源身份未核实', identity['category']
    if identity['level']: codes.append('source_type_level_' + str(identity['level']))
    repo = github_repo(url or '')
    if repo:
        org = next((o for o in lists.github_orgs if o.owner.casefold() == repo[0]), None)
        if org:
            codes.append('listed_repository_owner_requires_type_evidence')
        # A known organization is search priority, not a publisher type or proof
        # that this particular repository is an original textbook.
    if _COMMUNITY.search(host + path) and not _host_is(host, 'wikipedia.org'):
        credibility = min(credibility, 3); codes.append('community_content')
    if _PERSONAL.search(host + path):
        credibility = min(credibility, 2); codes.append('author_unverified')
    if any(_host_is(host, h) for h in _AGGREGATORS):
        credibility = min(credibility, 3); codes.append('publication_or_author_unverified')
    if _host_is(host, 'wikipedia.org') and (':' in path or '/w/' in path):
        credibility = min(credibility, 2); codes.append('not_encyclopedia_article')
    all_reasons = set(reasons) | (set(hit.reasons) if hit else set())
    if ((not hit and url) or (hit and hit.tier in {'low', 'blocked'}) or all_reasons & {'possible_fork', 'repost_site', 'piracy'}
            or _REPOST.search(title + '\n' + body[:4000])):
        credibility = min(credibility, 1); codes.append('non_original_or_disallowed')
    if not body.strip():
        credibility = 0; codes.append('body_unavailable')
    # A verified host/resource description is not the lesson itself. These
    # patterns were observed on real course download/video landing pages.
    landing_only = (is_landing_page(title, body) or (bool(url) and path in {'', '/'})
                    or (host == 'modelcontextprotocol.io' and bool(re.fullmatch(r'/specification/20\d\d-\d\d-\d\d/?', path)))
                    or bool(re.search(r'/(?:category|tags?)/', path, re.I)))
    if landing_only:
        codes.append('lesson_body_unavailable')
    if '/_sources/' in path:
        credibility = 1; codes.append('documentation_source_markup')
    if warnings:
        codes.append('extraction_warnings')

    # Current medicine, latest technology, policies and exam editions need an
    # explicit publication date. Evergreen mathematics/history do not expire.
    # Calendar dates in the goal are the study schedule, not a content
    # edition; only a bare year ("2025 考试大纲") asks for current material.
    topic = _SCHEDULE_DATE.sub(' ', goal)
    protocol_current = bool(re.search(r'model context protocol|\bMCP\b', topic, re.I))
    time_sensitive = protocol_current or bool(re.search(r'医学|医疗|高血压|治疗|用药|临床|最新|现行|今年|考试大纲|政策|'
                                     r'hypertension|medical|treatment|clinical|latest|current|\b20[2-9]\d\b', topic, re.I))
    freshness = 'not_required'
    if time_sensitive:
        # Versioned protocol pages are maintained by a current site build.
        # Their HTML modified date can be yesterday while the protocol text is
        # from an obsolete revision. The version in the URL is decisive.
        dated_version = re.search(r'/specification/(20\d\d-\d\d-\d\d)(?:/|\.|$)', path)
        if protocol_current and dated_version:
            published_at = dated_version[1]
        else:
            published_at = published_at or body_publication_date(body)
        if not published_at:
            if dated_version and (parsed_date := _date_value(dated_version[1])):
                published_at = parsed_date.isoformat()
        if not published_at:
            dated_path = re.search(r'/(20\d\d)/(\d\d)/(\d\d)/', path)
            if dated_path and (parsed_date := _date_value('-'.join(dated_path.groups()))):
                published_at = parsed_date.isoformat()
        try:
            age = (today - date.fromisoformat(published_at[:10])).days
            freshness = 'current' if 0 <= age <= (365 if protocol_current else 730) else 'stale'
        except (ValueError, TypeError):
            freshness = 'unknown'
        if freshness != 'current':
            credibility = min(credibility, 3); codes.append('freshness_' + freshness)

    # Match goal and each module separately: one relevant module is useful, but
    # a body-only name-drop or generic keyword cannot pass on its own.
    # Some official portals put navigation and field labels before the actual
    # record heading. Keep this bounded to the first few lines, not the whole
    # page where unrelated sidebar links could fabricate a title match.
    title_text = title.casefold() + ' ' + ' '.join(body.splitlines()[:8])[:650].casefold()
    if headings:
        # A chapter can contain a relevant section well after an unrelated
        # introduction. Use real extracted headings, never arbitrary nav text.
        title_text += ' ' + ' '.join(headings).casefold()
    body_text = body.casefold()
    scopes = [goal] + [m.get('title', '') + ' ' + m.get('summary', '') if isinstance(m, dict) else str(m) for m in modules]
    matches, coverage = [], []
    relevance = 1
    for index, scope in enumerate(scopes):
        wanted = terms(scope)
        present = sorted(t for t in wanted if _occurrences(body_text, t))
        headings = [t for t in present if _occurrences(title_text, t)]
        ratio = len(present) / max(1, len(wanted))
        single_clear = len(wanted) == 1 and headings and _occurrences(body_text, present[0]) >= 2
        score = 5 if ratio >= .8 and len(headings) >= 2 else 4 if single_clear or (ratio >= .45 and headings and len(present) >= 2) else 3 if present else 1
        relevance = max(relevance, score)
        if score >= 4: coverage.append(index)
        matches += present
    level_fit = 'unknown'
    if level in {'zero', 'beginner'} and re.search(r'advanced|graduate|research paper|高阶|研究生|前沿研究', title, re.I):
        relevance = min(relevance, 3); level_fit = 'too_advanced'; codes.append('level_mismatch')
    elif level in {'advanced', 'intermediate'} and re.search(r'absolute beginner|零基础|初识', title, re.I):
        relevance = min(relevance, 3); level_fit = 'review_only'; codes.append('level_mismatch')
    elif relevance >= 4:
        level_fit = 'no_obvious_mismatch'
    if landing_only:
        relevance = 1
    excerpt = body[:1800]
    for word in sorted(set(matches), key=len, reverse=True):
        at = body_text.find(word)
        if at >= 0:
            excerpt = body[max(0, at-120):at+1400]; break
    cross_language = bool(re.search(r'[\u4e00-\u9fff]', goal)) != bool(re.search(r'[\u4e00-\u9fff]', title))
    review = (not landing_only and credibility >= MIN_CREDIBILITY and level_fit not in {'too_advanced', 'review_only'}
              and (relevance == 3 or (relevance == 1 and cross_language)))
    eligible = credibility >= MIN_CREDIBILITY and relevance >= MIN_RELEVANCE
    if credibility < MIN_CREDIBILITY: codes.append('authority_below_threshold')
    if relevance < MIN_RELEVANCE: codes.append('relevance_unresolved' if review else 'relevance_below_threshold')
    note = f'{publisher}；可信 {credibility}/5，相关 {relevance}/5。'
    if not eligible:
        note += '尚未达到权威推荐门槛。'
    if freshness in {'unknown', 'stale'}: note += '此主题需较新资料，发布日期未知或超过两年。'
    return dict(version=1, credibility=credibility, relevance=relevance, eligible=eligible, needs_review=review,
                context=dict(goal=goal, level=level, scopes=scopes),
                publisher=publisher, source_type=category, originality='unconfirmed' if credibility < 4 else 'publisher_original',
                freshness=freshness, published_at=published_at, level_fit=level_fit, covered_scopes=coverage,
                reasons=list(dict.fromkeys(codes)), note=note, method='rules', url=url,
                evidence=dict(title=title, excerpt=excerpt, matched_terms=sorted(set(matches))))


def assess(source, goal, *, level='', modules=(), today=None, lists=None):
    return _assess(source.final_url, source.title, '\n'.join(b.text for b in source.document.blocks),
        goal=goal, level=level, modules=modules, today=today, lists=lists, reasons=source.reasons,
        published_at=getattr(source, 'published_at', None), warnings=source.document.warnings,
        headings=[b.text for b in source.document.blocks if getattr(b, 'kind', '') == 'heading'])


def assess_stored(source, chunks, goal, *, level='', modules=(), today=None, lists=None):
    return _assess(source.get('final_url') or source.get('url'), source.get('title', ''), '\n'.join(c['text'] for c in chunks),
        goal=goal, level=level, modules=modules, today=today, lists=lists, reasons=source.get('reasons', ()),
        published_at=source.get('published_at'), warnings=source.get('warnings', ()),
        headings=[c['text'] for c in chunks if c.get('kind') == 'heading'],
        publisher_evidence=source.get('publisher_evidence'))


def apply_review(quality, review):
    """Use existing scoring agent only for unresolved relevance, never authority.

    Retain its explanation alongside the exact fetched evidence for review.
    Missing/empty explanations fail closed. A model cannot override origin/date.
    """
    if not quality['needs_review']:
        return quality
    result = dict(quality)
    note = review.get('note', '')
    relevance = review.get('relevance')
    if type(relevance) is not int or not 1 <= relevance <= 5 or not isinstance(note, str) or len(note.strip()) < 10:
        return result
    result.update(relevance=relevance, needs_review=False, method='rules+model_relevance', review_note=note,
                  eligible=quality['credibility'] >= MIN_CREDIBILITY and relevance >= MIN_RELEVANCE,
                  note=quality['note'].split('；')[0] + f'；可信 {quality["credibility"]}/5，相关 {relevance}/5。' + note)
    return result


def annotate(store, plan_id, source_ids, *, lists=None, today=None):
    """Persist user-material assessments; return display rows, never reject uploads."""
    plan = store.get_plan(plan_id)
    modules = store.records('modules', plan_id)
    chunks = store.records('source_chunks', plan_id)
    rows = []
    with store.transaction():
        for source in store.records('sources', plan_id):
            if source['id'] not in source_ids: continue
            quality = assess_stored(source, [c for c in chunks if c['source_id'] == source['id']], plan['goal'],
                                     level=plan['level'], modules=modules, lists=lists, today=today)
            quality['user_provided'] = True
            quality['note'] += ' 用户提供的资料已保留；评分不足不等于已核实为权威。'
            store.add_sources(plan_id, [dict(source, quality=quality, user_provided=True)])
            rows.append(dict(source_id=source['id'], title=source['title'], **quality))
    return rows
