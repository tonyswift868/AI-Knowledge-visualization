"""Exact pressure source MathML regressions; no network/model response."""
import pytest
from refactor.learning.course.extract import extract_html


@pytest.mark.parametrize('unit',['N/m','lb/in'])
def test_extract38_literal_pressure_unit_does_not_gain_false_parentheses(unit):
    html=f'<main><p><math><msup><mtext>{unit}</mtext><mrow><mn>2</mn></mrow></msup></math></p></main>'
    doc=extract_html(html,'https://openstax.org/books/college-physics-2e/pages/11-3-pressure')
    assert doc.blocks[0].text==unit+'^2'


def test_extract38_structured_algebraic_ratio_keeps_grouping():
    html='<main><p><math><msup><mfrac><mi>x</mi><mi>y</mi></mfrac><mn>2</mn></msup></math></p></main>'
    assert extract_html(html,'https://offline.invalid').blocks[0].text=='(x/y)^2'
