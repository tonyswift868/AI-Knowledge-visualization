from dataclasses import replace

from refactor.learning.course import self_test
from refactor.learning.course.source_filter import directory_matches, load_lists


def test_openstax_duplicate_subject_labels_do_not_double_rank219():
    lists = load_lists()
    science = directory_matches('Science', lists=lists, limit=1000)
    matches = [row['id'] for row in science]
    assert matches.index('openstax-82') == 17


def test_title_only_uploaded_reference_goes_to_missing_answer219():
    answer = '# Sharding'
    raw = dict(source=answer, stem=answer, kind='recall', answer=answer)
    compiled = self_test.compile_row(raw, answer, gateway=None)
    assert compiled.get('missing') is True
    assert compiled['reason'] == '原文没有答案，补上答案后再加入。'


def test_bold_substantive_uploaded_answer_is_not_missing219():
    answer = '**Primary key**'
    raw = dict(source=answer, stem=answer, kind='recall', answer=answer)
    compiled = self_test.compile_row(raw, answer, gateway=None)
    assert compiled.get('missing') is not True
    assert compiled['key_points']


def test_heading_followed_by_substantive_answer_is_not_missing219():
    answer = '# Keys\n\nA primary key uniquely identifies each row.'
    raw = dict(source=answer, stem='# Keys', kind='recall', answer=answer)
    compiled = self_test.compile_row(raw, answer, gateway=None)
    assert compiled.get('missing') is not True
    assert compiled['key_points']


def test_duplicate_directory_labels_in_all_score_groups_do_not_change_rank219():
    lists = load_lists()
    for field in ('subjects', 'stages', 'keywords'):
        single = dict(id='a-single', subjects=[], stages=[], keywords=[])
        double = dict(id='b-double', subjects=[], stages=[], keywords=[])
        if field != 'subjects':
            single['subjects'] = ['Science']
            double['subjects'] = ['Science']
        single[field] = ['Science']
        double[field] = ['Science', 'Science']
        custom = replace(lists, directory=(single, double))
        matches = directory_matches('Science', lists=custom, limit=10)
        assert [row['id'] for row in matches] == ['a-single', 'b-double']

