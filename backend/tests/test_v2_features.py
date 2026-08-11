from __future__ import annotations

import asyncio
import base64
import json
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import db
from app.core_security import forbidden, validate_allowed_root, walk_safe_files
from app.grounding_service import critic, norm as grounding_norm
from app.ingestion_secure import chunks, consume_preview, create_preview, parse_file
from app.orm import ChunkORM, SessionLocal, SourceORM, WorkspaceORM, init_orm
from app.provider_service import AnthropicProvider, GeminiProvider, OpenAICompatibleProvider, ProviderError, get_provider
from app.retrieval_service import RetrievalService, tokenize
from app.url_fetcher_secure import MAX_BYTES, fetch_safe, validate_public_url
from app.services import available_days


def test_forbidden_names_cover_sensitive_files():
    assert forbidden('.env') and forbidden('id_rsa') and forbidden('secret.key')


def test_forbidden_names_allow_normal_file():
    assert not forbidden('lesson.md')


def test_root_rejects_drive_root():
    with pytest.raises(ValueError):
        validate_allowed_root('C:\\')


def test_root_rejects_user_home():
    with pytest.raises(ValueError):
        validate_allowed_root(str(Path.home()))


def test_walk_prunes_hidden_and_unsupported(tmp_path):
    (tmp_path / 'visible.md').write_text('ok', encoding='utf-8')
    (tmp_path / '.hidden.md').write_text('secret', encoding='utf-8')
    (tmp_path / 'image.png').write_bytes(b'png')
    accepted, rejected = walk_safe_files(tmp_path, tmp_path, {'.md'})
    assert [Path(x['path']).name for x in accepted] == ['visible.md']
    assert len(rejected) == 2


def test_walk_enforces_file_size(tmp_path):
    p = tmp_path / 'large.md'
    p.write_bytes(b'x' * 20)
    accepted, rejected = walk_safe_files(tmp_path, tmp_path, {'.md'}, max_bytes=10)
    assert not accepted and rejected


def test_walk_enforces_file_count(tmp_path):
    for name in ('a.md', 'b.md'):
        (tmp_path / name).write_text(name, encoding='utf-8')
    accepted, rejected = walk_safe_files(tmp_path, tmp_path, {'.md'}, max_files=1)
    assert len(accepted) == 1 and len(rejected) == 1


def test_parse_markdown_has_null_page(tmp_path):
    p = tmp_path / 'lesson.md'
    p.write_text('# Heading\n\nBody', encoding='utf-8')
    parts, status, pages = parse_file(p)
    assert status == 'ready' and pages == 0 and parts[0]['page_start'] is None
    assert parts[0]['heading'] == 'Heading'


def test_parse_html_strips_script(tmp_path):
    p = tmp_path / 'lesson.html'
    p.write_text('<html><title>T</title><script>bad</script><p>Good text</p></html>', encoding='utf-8')
    parts, status, _ = parse_file(p)
    assert status == 'ready' and 'bad' not in parts[0]['text'] and 'Good text' in parts[0]['text']


def test_parse_docx_has_null_page(tmp_path):
    from docx import Document
    p = tmp_path / 'lesson.docx'
    d = Document(); d.add_paragraph('Document body'); d.save(p)
    parts, status, _ = parse_file(p)
    assert status == 'ready' and parts[0]['page_end'] is None and 'Document body' in parts[0]['text']


def test_parse_pdf_records_real_page_ranges(tmp_path):
    import fitz
    p = tmp_path / 'lesson.pdf'
    d = fitz.open(); d.new_page().insert_text((72, 72), 'A' * 50); d.new_page().insert_text((72, 72), 'B' * 50); d.save(p)
    parts, status, page_count = parse_file(p)
    assert status == 'ready' and page_count == 2
    assert [(x['page_start'], x['page_end']) for x in parts] == [(1, 1), (2, 2)]


def test_parse_scan_pdf_marks_ocr(tmp_path):
    import fitz
    p = tmp_path / 'scan.pdf'
    d = fitz.open(); d.new_page(); d.save(p)
    parts, status, page_count = parse_file(p)
    assert parts == [] and status == 'needs_ocr' and page_count == 1


def test_chunks_preserve_page_metadata():
    result = chunks([{'text': 'one\n\ntwo', 'page_start': 4, 'page_end': 4, 'heading': 'H'}], 's1')
    assert len(result) == 2 and all(x['page_start'] == 4 and x['page_end'] == 4 for x in result)


def test_preview_requires_nonempty_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'db.sqlite')
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init_db()
    (tmp_path / 'a.md').write_text('a', encoding='utf-8')
    preview = create_preview(tmp_path, tmp_path)
    with pytest.raises(ValueError):
        consume_preview(preview['preview_id'], [])


def test_preview_rejects_unknown_file_id(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'db.sqlite')
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init_db(); (tmp_path / 'a.md').write_text('a', encoding='utf-8')
    preview = create_preview(tmp_path, tmp_path)
    with pytest.raises(ValueError):
        consume_preview(preview['preview_id'], ['unknown'])


def test_preview_detects_file_change(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'db.sqlite')
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init_db(); p = tmp_path / 'a.md'; p.write_text('a', encoding='utf-8')
    preview = create_preview(tmp_path, tmp_path); p.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError):
        consume_preview(preview['preview_id'], [preview['files'][0]['id']])


def test_preview_detects_expiration(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'db.sqlite')
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    db.init_db(); (tmp_path / 'a.md').write_text('a', encoding='utf-8')
    preview = create_preview(tmp_path, tmp_path)
    with db.connection() as conn:
        conn.execute('UPDATE local_previews SET expires_at=? WHERE id=?', ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), preview['preview_id']))
    with pytest.raises(ValueError):
        consume_preview(preview['preview_id'], [preview['files'][0]['id']])


def test_validate_url_rejects_non_http():
    with pytest.raises(ValueError):
        validate_public_url('file:///etc/passwd')


def test_validate_url_rejects_private_address(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, 0, 0, '', ('127.0.0.1', 0))])
    with pytest.raises(ValueError):
        validate_public_url('http://example.test')


def test_fetch_cleans_html_and_uses_user_agent(monkeypatch):
    class Response:
        status_code = 200
        headers = {'content-type': 'text/html'}
        content = b'<html><title>Title</title><script>bad</script><p>' + b'Useful content ' * 5 + b'</p></html>'
    class Client:
        def __init__(self, *args, **kwargs): self.kwargs = kwargs
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return Response()
    monkeypatch.setattr('app.url_fetcher_secure.httpx.AsyncClient', Client)
    monkeypatch.setattr('app.url_fetcher_secure.validate_public_url', lambda url: SimpleNamespace(scheme='http', hostname='example.test'))
    result = asyncio.run(fetch_safe('http://example.test'))
    assert 'bad' not in result['text'] and result['title'] == 'Title'


def test_fetch_rejects_large_content(monkeypatch):
    class Response:
        status_code = 200; headers = {'content-type': 'text/plain', 'content-length': str(MAX_BYTES + 1)}; content = b''
    class Client:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return Response()
    monkeypatch.setattr('app.url_fetcher_secure.httpx.AsyncClient', Client)
    monkeypatch.setattr('app.url_fetcher_secure.validate_public_url', lambda url: None)
    with pytest.raises(ValueError): asyncio.run(fetch_safe('http://example.test'))


def test_provider_factory_fake_does_not_keep_key():
    provider = get_provider({'provider': 'fake', 'api_key': 'secret'})
    assert provider.__class__.__name__ == 'FakeProvider' and not hasattr(provider, 'key')


def test_provider_factory_requires_real_config():
    with pytest.raises(ProviderError) as exc:
        get_provider({'provider': 'gemini', 'model': 'm'})
    assert exc.value.code == 'PROVIDER_CONFIG_MISSING'


def test_openai_provider_parses_response(monkeypatch):
    async def request(*args, **kwargs): return SimpleNamespace(status_code=200, json=lambda: {'choices': [{'message': {'content': 'ok'}}]})
    p = OpenAICompatibleProvider('https://provider.test', 'm', 'secret'); p.request = request
    assert asyncio.run(p.text([{'role': 'user', 'content': 'hi'}])) == 'ok'


def test_gemini_provider_parses_response():
    async def request(*args, **kwargs): return SimpleNamespace(status_code=200, json=lambda: {'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]})
    p = GeminiProvider('https://provider.test', 'm', 'secret'); p.request = request
    assert asyncio.run(p.text([{'role': 'user', 'content': 'hi'}])) == 'ok'


def test_anthropic_provider_parses_response():
    async def request(*args, **kwargs): return SimpleNamespace(status_code=200, json=lambda: {'content': [{'text': 'ok'}]})
    p = AnthropicProvider('https://provider.test', 'm', 'secret'); p.request = request
    assert asyncio.run(p.text([{'role': 'user', 'content': 'hi'}])) == 'ok'


def test_gemini_provider_returns_search_grounding_metadata():
    captured = {}

    async def request(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "modelVersion": "gemini-2.5-flash",
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": (
                                        "RESOURCE|point-1|official_practice|official|"
                                        "官方练习|可直接完成练习"
                                    )
                                }
                            ]
                        },
                        "groundingMetadata": {
                            "webSearchQueries": ["point 1 official practice"],
                            "groundingChunks": [
                                {
                                    "web": {
                                        "uri": "https://official.example/practice",
                                        "title": "Official Practice",
                                    }
                                }
                            ],
                            "groundingSupports": [
                                {
                                    "segment": {
                                        "startIndex": 0,
                                        "endIndex": 65,
                                        "text": "RESOURCE",
                                    },
                                    "groundingChunkIndices": [0],
                                }
                            ],
                        },
                    }
                ],
            },
        )

    provider = GeminiProvider("https://provider.test", "gemini-2.5-flash", "secret")
    provider.request = request
    result = asyncio.run(provider.grounded_web_search("find official practice"))
    assert captured["json"]["tools"] == [{"google_search": {}}]
    assert result["search_grounded"] is True
    assert result["sources"][0]["url"] == "https://official.example/practice"
    assert result["supports"][0]["source_indices"] == [0]
    assert result["queries"] == ["point 1 official practice"]


def test_real_providers_send_images_in_their_native_multimodal_payloads():
    image = {"media_type": "image/png", "data": b"\x89PNG\r\nvision"}

    openai_call = {}

    async def openai_request(*args, **kwargs):
        openai_call.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "openai vision"}}]},
        )

    openai = OpenAICompatibleProvider("https://provider.test", "m", "secret")
    openai.request = openai_request
    assert asyncio.run(openai.vision("read screenshot", [image])) == "openai vision"
    openai_content = openai_call["json"]["messages"][0]["content"]
    assert openai_content[0] == {"type": "text", "text": "read screenshot"}
    assert openai_content[1]["type"] == "image_url"
    assert openai_content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert base64.b64decode(
        openai_content[1]["image_url"]["url"].split(",", 1)[1]
    ) == image["data"]

    gemini_call = {}

    async def gemini_request(*args, **kwargs):
        gemini_call.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {
                "candidates": [{"content": {"parts": [{"text": "gemini vision"}]}}]
            },
        )

    gemini = GeminiProvider("https://provider.test", "m", "secret")
    gemini.request = gemini_request
    assert asyncio.run(gemini.vision("read screenshot", [image])) == "gemini vision"
    gemini_parts = gemini_call["json"]["contents"][0]["parts"]
    assert gemini_parts[0] == {"text": "read screenshot"}
    assert gemini_parts[1]["inline_data"]["mime_type"] == "image/png"
    assert base64.b64decode(gemini_parts[1]["inline_data"]["data"]) == image["data"]

    anthropic_call = {}

    async def anthropic_request(*args, **kwargs):
        anthropic_call.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"content": [{"text": "anthropic vision"}]},
        )

    anthropic = AnthropicProvider("https://provider.test", "m", "secret")
    anthropic.request = anthropic_request
    assert (
        asyncio.run(anthropic.vision("read screenshot", [image]))
        == "anthropic vision"
    )
    anthropic_content = anthropic_call["json"]["messages"][0]["content"]
    assert anthropic_content[0]["type"] == "image"
    assert anthropic_content[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(anthropic_content[0]["source"]["data"]) == image["data"]
    assert anthropic_content[1] == {"type": "text", "text": "read screenshot"}


def test_provider_http_error_is_structured():
    async def request(*args, **kwargs): return SimpleNamespace(status_code=429, json=lambda: {})
    p = OpenAICompatibleProvider('https://provider.test', 'm', 'secret'); p.request = request
    with pytest.raises(ProviderError) as exc: asyncio.run(p.text([]))
    assert exc.value.code == 'PROVIDER_RATE_LIMITED' and exc.value.status == 429


def test_tokenizer_supports_chinese_and_english():
    tokens = tokenize('Python 数据结构')
    assert 'python' in tokens and any('数据' in x for x in tokens)


def test_grounding_normalizes_whitespace():
    assert grounding_norm(' A\n B ') == 'a b'


def test_orm_source_hash_is_unique_per_workspace():
    init_orm(); wid = 'w_' + uuid4().hex; sid = 's_' + uuid4().hex
    with SessionLocal() as s:
        s.add(WorkspaceORM(id=wid, title='test', description=''))
        s.add(SourceORM(id=sid, workspace_id=wid, title='t', source_type='text', trust_level='reference', content_hash='hash', status='ready', page_count=0))
        s.commit()
        row = s.get(SourceORM, sid)
        assert row and row.content_hash == 'hash'


def test_retrieval_is_workspace_isolated():
    init_orm(); wid = 'w_' + uuid4().hex; other = 'w_' + uuid4().hex; sid = 's_' + uuid4().hex; cid = 'c_' + uuid4().hex
    with SessionLocal() as s:
        s.add_all([WorkspaceORM(id=wid, title='one', description=''), WorkspaceORM(id=other, title='two', description='')])
        s.add_all([SourceORM(id=sid, workspace_id=wid, title='Python', source_type='text', trust_level='authoritative', content_hash=uuid4().hex, status='ready', page_count=0), ChunkORM(id=cid, source_id=sid, ordinal=1, page_start=None, page_end=None, heading='Python', text='Python retrieval evidence', normalized_text='python retrieval evidence', content_hash=uuid4().hex)])
        s.commit()
    result = RetrievalService().search(wid, 'Python', top_k=1)
    assert len(result) == 1 and result[0]['source_id'] == sid and not RetrievalService().search(other, 'Python')


def test_retrieval_respects_top_k():
    init_orm(); wid = 'w_' + uuid4().hex; sid = 's_' + uuid4().hex
    with SessionLocal() as s:
        s.add(WorkspaceORM(id=wid, title='one', description=''))
        s.add(SourceORM(id=sid, workspace_id=wid, title='Title', source_type='text', trust_level='reference', content_hash=uuid4().hex, status='ready', page_count=0))
        s.add_all([ChunkORM(id='c_'+uuid4().hex, source_id=sid, ordinal=i, page_start=None, page_end=None, heading='', text=f'Python item {i}', normalized_text=f'python item {i}', content_hash=uuid4().hex) for i in range(3)])
        s.commit()
    assert len(RetrievalService().search(wid, 'Python', top_k=2)) == 2


def test_grounding_critic_rejects_unknown_chunk():
    assert critic('missing', [{'chunk_id': 'x', 'source_id': 's', 'page_start': None, 'page_end': None, 'quote': 'x'}])['passed'] is False


def test_grounding_critic_reports_errors():
    result = critic('missing', [{'chunk_id': 'x', 'source_id': 's', 'page_start': None, 'page_end': None, 'quote': 'x'}])
    assert result['errors'] and result['unsupported_claims'] == []


def test_scheduler_honors_preferred_weekdays_and_unavailable():
    start = datetime.now(timezone.utc).date()
    days = available_days(start, start + timedelta(days=14), 5, {(start + timedelta(days=7)).isoformat()}, [0, 2, 4])
    assert all(day.weekday() in {0, 2, 4} for day in days)
    assert (start + timedelta(days=7)) not in days


def test_goal_rejects_past_deadline(client):
    response = client.post('/api/workspaces', json={'title': 'deadline test'})
    wid = response.json()['id']
    body = {'goal_statement': 'valid measurable goal', 'deadline': (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat(), 'success_criteria': ['criterion']}
    assert client.post(f'/api/workspaces/{wid}/goal', json=body).status_code == 422


def test_goal_requires_success_criteria(client):
    response = client.post('/api/workspaces', json={'title': 'criteria test'})
    wid = response.json()['id']
    body = {'goal_statement': 'valid measurable goal', 'deadline': (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat(), 'success_criteria': []}
    assert client.post(f'/api/workspaces/{wid}/goal', json=body).status_code == 422


def test_conflict_is_recorded_and_resolved(client):
    wid=client.post('/api/workspaces',json={'title':'conflict test'}).json()['id']
    client.post(f'/api/workspaces/{wid}/sources/text',json={'title':'A','text':'# Topic\n\nAlpha evidence','trust_level':'reference'})
    client.post(f'/api/workspaces/{wid}/sources/text',json={'title':'B','text':'# Topic\n\nBeta evidence','trust_level':'reference'})
    conflicts=client.get(f'/api/workspaces/{wid}/conflicts').json()
    assert len(conflicts)==1 and conflicts[0]['status']=='unresolved'
    chosen=conflicts[0]['source_a_id']; response=client.post(f"/api/conflicts/{conflicts[0]['id']}/resolve",json={**conflicts[0],'selected_source_id':chosen})
    assert response.status_code==200 and response.json()['status']=='resolved'


def test_job_query_returns_persisted_import_job(client):
    wid=client.post('/api/workspaces',json={'title':'job test'}).json()['id']
    result=client.post(f'/api/workspaces/{wid}/sources/text',json={'title':'job source','text':'job evidence text'}).json()
    job=client.get(f"/api/jobs/{result['job_id']}")
    assert job.status_code==200 and job.json()['status']=='succeeded' and job.json()['progress']==100
