"""Fixed, manually labelled sources; neither network nor model grading."""
from datetime import date
from types import SimpleNamespace

import pytest

from refactor.learning.course import source_quality as q

TODAY = date(2026, 9, 29)
BODY = 'Python functions parameters\nFunctions accept parameters and return values. ' * 8


def score(url='https://docs.python.org/3/tutorial/controlflow.html', *, title='Python functions parameters',
          body=BODY, goal='Python functions parameters', published_at=None, reasons=(), **kwargs):
    source = SimpleNamespace(final_url=url, title=title, reasons=reasons, published_at=published_at,
                             document=SimpleNamespace(blocks=[SimpleNamespace(text=body)], warnings=()))
    return q.assess(source, goal, today=TODAY, **kwargs)


@pytest.mark.parametrize('url,expected', [
    ('https://docs.python.org/3/tutorial/controlflow.html', True),
    ('https://docs.python.org.attacker.test/guide', False),
    ('https://unknown.test/python', False),
    ('https://adg.csdn.net/python', False),
    ('https://learn.microsoft.com/en-us/answers/questions/12', True),
    ('https://mit.edu/~student/python', False),
    ('https://arxiv.org/abs/2401.00001', False),
    ('https://github.com/datawhalechina/hello-agents', False),  # owner priority alone is not publisher evidence
    ('https://github.com/wshao12/hello-agents', False),
    ('https://github.com/datawhalechina/hello-agents/issues/1', True),
    ('https://en.wikipedia.org/wiki/Talk:Python', False),
])
def test_fixed_authority_gold(url, expected):
    assert score(url)['eligible'] is expected


def test_origin_empty_body_and_unrelated_page():
    assert not score(reasons=('possible_fork',))['eligible']
    assert not score(body='转载：' + BODY)['eligible']
    assert not score(body='')['eligible']
    result = score(title='Roman pottery', body='Roman pottery archaeology')
    assert result['relevance'] == 1 and not result['needs_review']


def test_dates_are_publication_evidence_not_fetch_or_copyright():
    assert q.published_date('<p>Copyright 2026</p>') is None
    assert q.published_date('<meta property="article:published_time" content="2025-09-25T01:02:03Z">') == '2025-09-25'
    for date_value in (None, '2022-01-01', '2027-01-01'):
        assert not score(goal='current Python functions parameters', published_at=date_value)['eligible']
    assert score(goal='current Python functions parameters', published_at='2026-01-01')['eligible']


def test_study_schedule_dates_do_not_make_evergreen_goals_time_sensitive():
    # 24-MF06 P2-01: the requirement check writes the study window into the goal.
    for goal in ('Python functions parameters，在2026-10-05至2026-10-14期间学完',
                 'Python functions parameters 2026年10月5日到2026年10月14日', 'Python functions parameters 2026年10月学完'):
        assert score(goal=goal)['freshness'] == 'not_required'
    assert score(goal='Python functions parameters 2026 edition')['freshness'] != 'not_required'


def test_module_coverage_and_level():
    result = score(goal='软件开发完整学习路线', modules=[{'title': 'Python functions parameters'}])
    assert result['eligible'] and 1 in result['covered_scopes']
    assert not score(title='Advanced Python functions parameters', level='beginner')['eligible']


def test_model_cannot_elevate_authority_or_omit_reviewable_reason():
    arbitrary = score('https://unknown.test/python')
    model = {'relevance': 5, 'credibility': 5, 'note': 'The body explicitly teaches Python functions and parameters.'}
    assert not q.apply_review(arbitrary, model)['eligible']
    ambiguous = score(goal='Python functions parameters exceptions modules decorators generators')
    assert ambiguous['needs_review']
    assert not q.apply_review(ambiguous, dict(model, note='好'))['eligible']
    result = q.apply_review(ambiguous, model)
    assert result['eligible'] and result['evidence'] == ambiguous['evidence']
    assert result['credibility'] == ambiguous['credibility']


def test_user_file_keeps_unknown_identity_and_content():
    source = dict(id='s', title='Python functions parameters', url=None)
    quality = q.assess_stored(source, [{'text': BODY}], 'Python functions parameters')
    assert quality['credibility'] == 2 and not quality['eligible']


def test_real_course_resource_landing_patterns_cannot_be_admitted_by_model():
    for title, body in [
        ('MIT18_06SCF11_Ses2.8prob.pdf | Linear Algebra | MIT OpenCourseWare',
         'This resource contains problem set related to eigenvalues and eigenvectors.\nDownload File'),
        ('Linear Algebra eigenvalues eigenvectors',
         'Special matrices have special eigenvalues and eigenvectors.\nYour browser does not support some features required to play this video.'),
    ]:
        result=score('https://ocw.mit.edu/courses/18-06/resources/lecture', title=title, body=body,
                     goal='linear algebra eigenvalues eigenvectors')
        assert not result['eligible'] and not result['needs_review']
        assert 'lesson_body_unavailable' in result['reasons']


def test_level_mismatch_is_not_overridden_by_model():
    result=score(title='Advanced Python functions parameters',level='beginner')
    assert not result['needs_review']
    assert not q.apply_review(result, {'relevance':5,'note':'This course contains Python functions and parameters.'})['eligible']
