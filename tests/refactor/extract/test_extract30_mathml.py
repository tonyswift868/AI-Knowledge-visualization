"""Offline mathematical-source regressions; no network/model calls."""
import io
import threading

import pytest

from refactor.learning.course.extract import extract_html, extract_bytes, ExtractError


def text(markup):
    return '\n'.join(b.text for b in extract_html('<main><p>' + markup + '</p></main>', 'https://offline.invalid').blocks)


@pytest.mark.parametrize('math,expected', [
    ('<mfrac><mn>3</mn><mn>4</mn></mfrac>', '3/4'),
    ('<mfrac><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow><mn>2</mn></mfrac>', '(x+1)/2'),
    ('<msup><mi>x</mi><mn>2</mn></msup>', 'x^2'),
    ('<msub><mi>x</mi><mi>i</mi></msub>', 'x_i'),
    ('<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>', 'x_i^2'),
    ('<msqrt><mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow></msqrt>', '√(x+1)'),
    ('<mroot><mi>x</mi><mn>3</mn></mroot>', 'root(3, x)'),
    ('<mfenced><mi>x</mi><mi>y</mi></mfenced>', '(x,y)'),
    ('<mo>−</mo><mfrac><mn>3</mn><mn>8</mn></mfrac><mo>=</mo><mfrac><mn>1</mn><mn>2</mn></mfrac>', '−3/8=1/2'),
    ('<munder><mo>∑</mo><mi>i</mi></munder><mo>×</mo><mi>x</mi>', '∑_i×x'),
    ('<mtable><mtr><mtd><mi>x</mi></mtd><mtd><mi>y</mi></mtd></mtr></mtable>', 'x | y'),
    ('<mn>1</mn><mfrac><mn>1</mn><mn>2</mn></mfrac>', '1 1/2'),
    ('<mfrac><mn>1</mn><mn>2</mn></mfrac><mi>x</mi>', '(1/2)x'),
    ('\n <mrow>\n <mi>x</mi>\n <mo>+</mo>\n <mn>1</mn>\n </mrow>\n', 'x+1'),
])
def test_mathml_readable_structure(math, expected):
    assert text('<math>' + math + '</math>') == expected


def test_openstax_semantics_and_accessible_copies_keep_one_formula_and_tail():
    fraction = '<mrow><mi>y</mi><mo>=</mo><mfrac><mn>3</mn><mn>4</mn></mfrac></mrow>'
    math = '<math><semantics>' + fraction + '<annotation-xml encoding="MathML-Content">' + fraction + '</annotation-xml><annotation encoding="TeX">y=3/4</annotation></semantics></math>'
    markup = 'Is <span class="os-math-in-para">' + math + '<span class="sr-only">y equals three quarters</span></span> a solution?'
    assert text(markup) == 'Is y=3/4 a solution?'
    assert text('Before <mjx-container><span aria-hidden="true">34</span><mjx-assistive-mml aria-hidden="true">' + math + '</mjx-assistive-mml></mjx-container> after') == 'Before y=3/4 after'
    assert text('<span class="katex"><span class="katex-mathml">' + math + '</span><span class="katex-html">34</span></span>') == 'y=3/4'


def test_math_in_table_heading_cancel_and_chunk_determinism():
    html = '<main><h1>分数 <math><mfrac><mn>3</mn><mn>4</mn></mfrac></math></h1><table><tr><td>x</td><td><math><msup><mi>x</mi><mn>2</mn></msup></math></td></tr></table></main>'
    a, b = [extract_html(html, 'https://offline.invalid') for _ in range(2)]
    assert a == b
    assert a.blocks[-1].text == 'x | x^2'
    assert a.blocks[-1].section == ('分数 3/4',)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(ExtractError, match='取消') as caught:
        extract_html(html, 'https://offline.invalid', cancel=cancel)
    assert caught.value.code == 'CANCELLED'


def test_docx_plain_fractions_preserved_without_html_math_rewrite():
    from docx import Document
    document = Document(); document.add_paragraph('y=3/4; n-3/8=1/2; ¾')
    data = io.BytesIO(); document.save(data)
    assert extract_bytes(data.getvalue(), 'fractions.docx').blocks[0].text == 'y=3/4; n-3/8=1/2; ¾'


def test_pdf_plain_fractions_preserved_without_html_math_rewrite():
    from tests.refactor.extract.test_extract import pdf_bytes
    result = extract_bytes(pdf_bytes(['Fractions: y=3/4; n-3/8=1/2; 3/4 is not 34.']), 'fractions.pdf')
    assert 'y=3/4; n-3/8=1/2;' in result.blocks[0].text


def test_docx_office_math_fraction_superscript_root_order_and_table():
    from docx import Document
    from docx.oxml import parse_xml
    def math(body):
        return parse_xml('<m:oMath xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'+body+'</m:oMath>')
    def atom(value):return '<m:r><m:t>'+value+'</m:t></m:r>'
    fraction='<m:f><m:num>'+atom('3')+'</m:num><m:den>'+atom('4')+'</m:den></m:f>'
    doc=Document();p=doc.add_paragraph('分数 y=')
    p._p.append(math(fraction));p.add_run('，不是34。')
    p=doc.add_paragraph('平方：');p._p.append(math('<m:sSup><m:e>'+atom('x')+'</m:e><m:sup>'+atom('2')+'</m:sup></m:sSup>'))
    p=doc.add_paragraph('根号：');p._p.append(math('<m:rad><m:radPr/><m:deg/><m:e>'+atom('x+1')+'</m:e></m:rad>'))
    p=doc.add_paragraph('立方根：');p._p.append(math('<m:rad><m:deg>'+atom('3')+'</m:deg><m:e>'+atom('x')+'</m:e></m:rad>'))
    p=doc.add_paragraph('带分数：');p._p.append(math(atom('1')+fraction))
    doc.add_table(rows=1,cols=1).cell(0,0).paragraphs[0]._p.append(math(fraction))
    output=io.BytesIO();doc.save(output)
    result=extract_bytes(output.getvalue(),'office-math.docx')
    assert [b.text for b in result.blocks]==['分数 y=3/4，不是34。','平方：x^2','根号：√(x+1)','立方根：root(3, x)','带分数：1 3/4','3/4']
    assert result.blocks[-1].kind=='table'
