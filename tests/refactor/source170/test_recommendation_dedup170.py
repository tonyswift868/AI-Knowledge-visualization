from refactor.learning.course.research_pipeline import prepare_recommendations
from refactor.learning.course.source_filter import (
    github_recommendation_key,
    is_noncontent_github_url,
    recommendation_rejection,
)


def card(url, title='学习资料'):
    return {'url': url, 'title': title, 'type': '文档', 'why': '用于学习'}


def test_170_github_repo_aliases_share_recommendation_identity():
    urls = [
        'https://github.com/TonySwift868/MentorFlow-Guide.git/',
        'https://github.com/tonyswift868/mentorflow-guide/tree/main',
        'https://github.com/tonyswift868/mentorflow-guide/tree/main/docs/guide',
        'https://github.com/tonyswift868/mentorflow-guide/blob/main/README.md',
        'https://github.com/tonyswift868/mentorflow-guide/wiki',
    ]
    assert {github_recommendation_key(url) for url in urls} == {
        'github:tonyswift868/mentorflow-guide'
    }


def test_170_management_pages_rejected_but_wiki_content_is_not_assumed_empty():
    for route in ('releases', 'tags', 'issues', 'pulls', 'actions'):
        url = f'https://github.com/example/guide/{route}'
        assert is_noncontent_github_url(url)
        assert recommendation_rejection(url)
    # GitHub wikis can contain real course material. They deduplicate repository
    # identity but are not rejected as empty by route name alone.
    wiki = 'https://github.com/example/guide/wiki'
    assert not is_noncontent_github_url(wiki)
    assert not recommendation_rejection(wiki)


def test_170_provided_repo_excludes_all_pages_and_does_not_consume_candidate_slot():
    provided = [card('https://github.com/TonySwift868/MentorFlow-Guide.git')]
    rows = [
        card('https://github.com/tonyswift868/mentorflow-guide/tree/main/docs'),
        card('https://github.com/tonyswift868/mentorflow-guide/releases'),
        card('https://github.com/other/guide/tree/main/docs'),
    ]
    result = prepare_recommendations(rows, provided=provided)
    assert [item['url'] for item in result] == ['https://github.com/other/guide/tree/main/docs']


def test_170_candidates_dedupe_by_repository_and_distinct_repositories_remain():
    rows = [
        card('https://github.com/example/guide'),
        card('https://github.com/EXAMPLE/guide.git/tree/main/README.md'),
        card('https://github.com/another/guide/tree/main'),
    ]
    result = prepare_recommendations(rows)
    assert [item['url'] for item in result] == [
        'https://github.com/example/guide',
        'https://github.com/another/guide/tree/main',
    ]


def test_170_previous_repo_dedup_keeps_existing_text_fallback_semantics():
    previous = [card('https://github.com/example/guide')]
    rows = [card('https://github.com/example/guide/tree/main'),
            card('https://youtube.com/watch?v=lesson')]
    assert prepare_recommendations(rows, previous=previous) == []


def test_170_previous_video_url_keeps_video_fallback():
    result = prepare_recommendations(
        [card('https://youtube.com/watch?v=current-video')],
        previous=['https://youtube.com/watch?v=previous-video'],
    )
    assert len(result) == 1
    assert result[0]['is_video'] is True
