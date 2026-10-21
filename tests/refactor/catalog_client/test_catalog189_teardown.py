"""A closed catalog cannot recreate its data root from a late response."""
import shutil
from threading import Event

import httpx

from refactor.learning.course.catalog_client import CatalogClient
from tests.refactor.catalog_client.test_catalog_client import CA, session


def test_catalog189_close_fences_late_refresh_state_write(tmp_path):
    entered, release = Event(), Event()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        entered.set()
        assert release.wait(5), "test did not release the blocked fake response"
        return httpx.Response(503)

    identity = session()
    root = tmp_path / "isolated-data-root"
    root.mkdir()
    catalog = CatalogClient(
        account_id=identity.identity.account_id,
        data_root=root,
        session_provider=lambda: identity,
        api_base_url="https://account.example.test",
        ca_bundle=CA,
        http_transport=httpx.MockTransport(handler),
    )
    failures, retry_at = catalog._failures, catalog._retry_at
    future = catalog.refresh_background()
    try:
        assert entered.wait(2), "refresh did not enter the mock transport"
        catalog.close()
        assert not future.done(), "close must not wait for a blocked network request"
        shutil.rmtree(root)
        release.set()
        future.result(timeout=3)

        assert calls == ["/api/enterprise/catalog/source-whitelist"]
        assert catalog._failures == failures
        assert catalog._retry_at == retry_at
        assert not root.exists()
        assert not catalog.cache_dir.exists()
    finally:
        release.set()
        catalog.close()
