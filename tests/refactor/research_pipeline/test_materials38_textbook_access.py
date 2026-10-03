from types import SimpleNamespace
import httpx
import pytest
from refactor.learning.course.research import (ResearchService,ResearchUploadRequired,
    smartedu_metadata_url,material_upload_guidance,directory_upload_guidance,directory_candidates)
from refactor.learning.course.research_pipeline import ResearchPipeline
from refactor.learning.course.source_filter import load_lists
from refactor.learning.course.catalog_client import CatalogClient

PAGE='https://basic.smartedu.cn/tchMaterial/detail?contentType=assets_document&contentId=aec6de38-33d2-417a-bbdb-e39154a046a9&catalogType=tchMaterial&subCatalog=tchMaterial'
PDF='https://r1-ndr-private.ykt.cbern.com.cn/edu_product/esp/assets/book.pdf'


def service(status=200,metadata=None):
    calls=[]
    def reply(request):
        calls.append(request)
        assert request.method=='GET'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert 'Chrome/' in request.headers['user-agent'] and 'zh-CN' in request.headers['accept-language']
        if request.url.path.endswith('.json'):
            return httpx.Response(200,json=metadata or dict(title='物理八年级下册',ti_items=[dict(ti_format='pdf',ti_is_source_file=True,ti_storage=PDF)]))
        return httpx.Response(status,content=b'%PDF-fixture',headers={'content-type':'application/pdf'})
    return ResearchService(transport=httpx.MockTransport(reply)),calls


def test_materials38_public_metadata_pdf_uses_existing_byte_fetch():
    reader,calls=service()
    result=reader.fetch_bytes(dict(url=PAGE,title='物理八年级下册'))
    assert result.url==PAGE and result.final_url==PDF and result.body==b'%PDF-fixture'
    assert len(calls)==2 and str(calls[0].url)==smartedu_metadata_url(PAGE)


@pytest.mark.parametrize('status',[401,403])
def test_materials38_protected_resource_has_upload_guide_no_auth_workaround(status):
    reader,calls=service(status)
    with pytest.raises(ResearchUploadRequired) as caught:reader.fetch_bytes(dict(url=PAGE))
    assert len(calls)==2
    assert '物理八年级下册' in str(caught.value) and PAGE in str(caught.value)
    assert '上传资料' in str(caught.value) and f'HTTP {status}' in str(caught.value)


def test_materials38_cs_path_mapping_only_advertised_resource():
    reader,calls=service(metadata=dict(title='物理教材',ti_items=[dict(ti_format='pdf',ti_file_flag='source',ti_storage='cs_path:${ref-path}/book.pdf')]))
    result=reader.fetch_bytes(dict(url=PAGE))
    assert result.final_url=='https://r1-ndr-private.ykt.cbern.com.cn/book.pdf' and len(calls)==2


def test_materials38_metadata_no_pdf_does_not_fetch_random_other_resource():
    reader,calls=service(metadata=dict(title='教材名称',ti_items=[dict(ti_format='folder',ti_storage=PDF)]))
    with pytest.raises(ResearchUploadRequired):reader.fetch_bytes(dict(url=PAGE))
    assert len(calls)==1


def test_materials38_pipeline_keeps_clear_upload_message_without_adopting_text():
    def fetch(url,**kw):raise ResearchUploadRequired(material_upload_guidance(url,'物理八年级下册'))
    with ResearchPipeline(fetcher=fetch) as pipeline:result=pipeline.run([dict(url=PAGE,title='物理八年级下册')])
    assert not result.verified and result.rejected[0].code=='material_upload_required'
    assert '上传资料' in result.rejected[0].message and PAGE in result.rejected[0].message


@pytest.mark.parametrize('url',['https://example.com/tchMaterial/detail?contentId=aec6de38-33d2-417a-bbdb-e39154a046a9',
    'https://basic.smartedu.cn/tchMaterial/detail?contentId=../../x','https://basic.smartedu.cn/tchMaterial'])
def test_materials38_only_real_book_uuid_maps_to_metadata(url):assert smartedu_metadata_url(url) is None


def test_materials38_directory_cap_preserves_verified_answer_source():
    hits=directory_candidates('初二物理压强 习题 真题 答案',limit=1)
    assert len(hits)==1 and hits[0].url=='https://www.shmeea.edu.cn/download/20190926/04.pdf'
    assert 'jyeoo' not in hits[0].url


def test_materials38_catalog_chat_guidance_contains_matched_book_and_link():
    texts=directory_upload_guidance('初二物理压强')
    assert any('八年级下册' in text and '/detail?' in text and '上传资料' in text for text in texts)
    fake=SimpleNamespace(load_lists=load_lists)
    assert CatalogClient.material_guidance(fake,'初二物理压强')==texts


def test_materials38_unrelated_source_is_not_given_false_login_guidance():
    assert material_upload_guidance('https://docs.python.org/3/tutorial/datastructures.html')==''
    assert directory_upload_guidance('Python 列表')==()


@pytest.mark.parametrize('url',['https://jiaoshi.bnupg.com/','https://1088.com.cn/',
    'https://www.sstp.com.cn/p/aboutus.html','https://www.fltrp.com/'])
def test_materials38_other_verified_publishers_offer_same_manual_upload_fallback(url):
    guide=material_upload_guidance(url,'所选教材')
    assert '出版社官网' in guide and url in guide and '上传资料' in guide
