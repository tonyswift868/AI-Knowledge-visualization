"""Release dependency fixes: version gates plus offline production-path smoke."""
from importlib.metadata import version
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[3]
FLOORS = {'brotli': '1.2.1', 'certifi': '2026.10.19', 'pypdf': '6.21.3', 'pillow': '13.0.0'}
LOCKS = ('refactor/desktop/requirements-account-shell.txt',
         'scripts/release/requirements-build.txt', 'scripts/release/requirements-installer.txt')


@pytest.mark.parametrize('name,minimum', FLOORS.items())
def test_release_pins_include_security_fixes219(name, minimum):
    found = []
    for filename in LOCKS:
        with (ROOT / filename).open(encoding='utf-8') as stream:
            for line in stream:
                package, sep, pinned = line.strip().partition('==')
                if sep and package.lower() == name:
                    assert Version(pinned) >= Version(minimum), filename
                    found.append(pinned)
    assert found
    assert len(set(found)) == 1, 'Runtime and build pins must agree'


@pytest.mark.parametrize('name,minimum', FLOORS.items())
def test_installed_dependency_security_version219(name, minimum):
    assert Version(version(name)) >= Version(minimum)


def test_httpx_brotli_decode219():
    import brotli
    from refactor.learning.course.research import ResearchService
    body = '<html><body>课程资料测试</body></html>'.encode()
    def respond(request):
        assert 'br' in request.headers['accept-encoding']
        return httpx.Response(200,
            headers={'Content-Encoding': 'br', 'Content-Type': 'text/html'},
            content=brotli.compress(body))
    service = ResearchService(transport=httpx.MockTransport(respond))
    assert service.fetch_bytes({'url': 'https://example.test/material'}).body == body


def test_updated_public_roots_keep_account_ca219(tmp_path):
    import certifi
    from refactor.desktop import shell_paths
    bundle = shell_paths._build_ca_bundle(tmp_path)
    content = bundle.read_bytes()
    assert content.startswith(Path(certifi.where()).read_bytes().rstrip())
    assert content.endswith(shell_paths.ACCOUNT_API_TRUST.read_bytes().strip() + b'\n')
    assert b'emSign ECC Root CA - G3' not in Path(certifi.where()).read_bytes()


def test_image_to_pdf_roundtrip219():
    from PIL import Image
    from pypdf import PdfReader
    image_bytes = BytesIO()
    Image.new('RGB', (32, 32), 'white').save(image_bytes, format='JPEG2000')
    with Image.open(BytesIO(image_bytes.getvalue())) as image:
        image.load()
        assert image.size == (32, 32)
        pdf = BytesIO()
        image.convert('RGB').save(pdf, format='PDF')
    reader = PdfReader(BytesIO(pdf.getvalue()))
    assert len(reader.pages) == 1
