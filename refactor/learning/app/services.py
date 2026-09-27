"""Account-scoped backends used by the new window.

Built once per bound account session and closed on logout/switch. Services are
created lazily so a broken optional part (for example an unreachable catalog)
never blocks the rest of the window.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from .settings import AppSettings

log = logging.getLogger(__name__)


class AppServices:
    def __init__(self, *, account_id: str, data_root, session_provider, api_base_url: str, ca_bundle):
        self.account_id = account_id
        self.data_root = Path(data_root)
        self.session_provider = session_provider
        self.api_base_url = api_base_url
        self.ca_bundle = ca_bundle
        self.settings = AppSettings(account_id, data_root)
        self._lock = threading.RLock()
        self._gateway = None
        self._store = None
        self._catalog = None
        self._chat = None
        self._closed = False

    # -- catalog (server whitelist and prices, cached, with built-in fallback)
    @property
    def catalog(self):
        with self._lock:
            if self._catalog is None and not self._closed:
                from ..course.catalog_client import CatalogClient
                self._catalog = CatalogClient(account_id=self.account_id, data_root=self.data_root,
                                              session_provider=self.session_provider,
                                              api_base_url=self.api_base_url, ca_bundle=self.ca_bundle)
            return self._catalog

    def _price_catalog(self):
        from ..connections.pricing import PriceCatalog
        try:
            return PriceCatalog.from_dict(self.catalog.load_prices())
        except Exception:  # noqa: BLE001 - prices are optional; usage is still counted
            log.warning("price catalog unavailable; costs will show as unpriced")
            return PriceCatalog.empty()

    # -- model gateway (presets, keys, usage log)
    @property
    def gateway(self):
        with self._lock:
            if self._gateway is None and not self._closed:
                from ..connections.gateway import ModelGateway
                self._gateway = ModelGateway(self.account_id, self.data_root, price_catalog=self._price_catalog())
            return self._gateway

    # -- course store (plans, progress, reports)
    @property
    def store(self):
        with self._lock:
            if self._store is None and not self._closed:
                from ..course.store import CourseStore
                self._store = CourseStore(self.account_id, self.data_root)
            return self._store

    # -- free chat sessions (same account path as the old tools page)
    @property
    def chat(self):
        with self._lock:
            if self._chat is None and not self._closed:
                from .chat import open_store
                self._chat = open_store(self.account_id, self.data_root)
            return self._chat

    def refresh_catalog(self):
        try:
            catalog = self.catalog
            if catalog is not None:
                catalog.refresh_background()
        except Exception:  # noqa: BLE001
            log.warning("catalog refresh could not start", exc_info=True)

    def close(self):
        with self._lock:
            self._closed = True
            services, self._gateway, self._store, self._catalog = (self._gateway, self._store, self._catalog), None, None, None
            self._chat = None
        for service in services:
            if service is not None:
                try:
                    service.close()
                except Exception:  # noqa: BLE001 - closing one must not stop the others
                    log.warning("service close failed", exc_info=True)
