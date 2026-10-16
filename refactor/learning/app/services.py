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
        self.settings = AppSettings(account_id, data_root, store_provider=lambda: self.store)
        self._lock = threading.RLock()
        self._gateway = None
        self._store = None
        self._catalog = None
        self._chat = None
        self._rag = None
        self._closed = False
        self.model_status_listener = None

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
                self._gateway.status_listener = self.model_status_listener
            return self._gateway

    def bind_model_status(self, listener):
        with self._lock:
            self.model_status_listener = listener
            if self._gateway is not None:
                self._gateway.status_listener = listener

    def model_status(self):
        # Rendering a status chip must not load the remote price catalogue.
        with self._lock:
            status=getattr(self._gateway,'model_status',None)
            return status() if callable(status) else dict(state='unknown',short='模型待验证')

    def clear_model_fault_scope(self, purpose, plan_id):
        with self._lock:
            clear=getattr(self._gateway,'clear_fault_scope',None)
            if callable(clear):clear(purpose,plan_id)

    # -- course store (plans, progress, reports)
    @property
    def store(self):
        with self._lock:
            if self._store is None and not self._closed:
                from ..course.store import CourseStore
                self._store = CourseStore(self.account_id, self.data_root)
            return self._store

    # -- free chat sessions (same CourseStore/outbox as the learning records)
    @property
    def chat(self):
        with self._lock:
            if self._chat is None and not self._closed:
                from .chat_store import CourseChatStore
                self._chat = CourseChatStore(self.store)
            return self._chat

    # -- document Q&A (local-only sessions; never part of CourseStore/outbox)
    @property
    def rag(self):
        with self._lock:
            if self._rag is None and not self._closed:
                from .rag import RagService
                self._rag = RagService(
                    store=self.store,
                    account_id=self.account_id,
                    session_provider=self.session_provider,
                    api_base_url=self.api_base_url,
                    ca_bundle=self.ca_bundle,
                    gateway=self.gateway,
                )
            return self._rag

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
            services, self._gateway, self._store, self._catalog, self._rag = (
                self._rag, self._gateway, self._catalog, self._store), None, None, None, None
            self._chat = None
        for service in services:
            if service is not None:
                try:
                    service.close()
                except Exception:  # noqa: BLE001 - closing one must not stop the others
                    log.warning("service close failed", exc_info=True)
