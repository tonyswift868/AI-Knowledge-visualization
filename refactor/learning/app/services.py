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
        self._model_probe_lock = threading.Lock()
        self._gateway = None
        self._store = None
        self._catalog = None
        self._chat = None
        self._rag = None
        self._closed = False
        self.model_status_listener = None
        self._model_probe_cancel = None
        self._model_probe_thread = None
        self._model_probe_generation = 0

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
            from ..connections.gateway import ModelGateway
            if self._gateway is None and not self._closed:
                self._gateway = ModelGateway(self.account_id, self.data_root, price_catalog=self._price_catalog())
                self._gateway.status_listener = self.model_status_listener
            return self._gateway

    def bind_model_status(self, listener):
        # Binding is a pointer swap only. Do not wait for the lazy gateway's
        # local catalog initialization while account scope events are handled.
        self.model_status_listener = listener
        gateway = self._gateway
        if gateway is not None:
            gateway.status_listener = listener

    def model_status(self):
        # Rendering a status chip must not load the remote price catalogue.
        gateway = self._gateway
        status = getattr(gateway, 'model_status', None)
        return status() if callable(status) else dict(state='unknown', short='模型待验证')

    def start_model_auto_check(self, *, retry_network=False):
        """Start a non-blocking connection probe for the active account.

        ``retry_network`` is reserved for the existing server/network recovery
        signal. It only repeats a probe whose last classified failure was a
        model-network failure; other fault categories remain user-triggered.
        The UI caller does not instantiate services or touch disk/credentials.
        """
        with self._model_probe_lock:
            if self._closed:
                return False
            current = self._model_probe_cancel
            if current is not None and not current.is_set():
                return False
            self._model_probe_generation += 1
            generation = self._model_probe_generation
            cancel = threading.Event()
            self._model_probe_cancel = cancel
            worker = threading.Thread(
                target=self._run_model_auto_check,
                args=(generation, cancel, bool(retry_network)),
                name=f'model-check-{self.account_id[:8]}', daemon=True)
            self._model_probe_thread = worker
            worker.start()
            return True

    def on_network_restored(self):
        """Retry only an automatic probe previously classified as network failure."""
        gateway = self._gateway
        if self._closed or gateway is None:
            return False
        status = gateway.model_status()
        fault = status.get('fault') if isinstance(status, dict) else None
        if not (isinstance(status, dict) and status.get('state') == 'error'
                and isinstance(fault, dict) and fault.get('category') == 'network'):
            return False
        return self.start_model_auto_check(retry_network=True)

    def model_configuration_changed(self):
        """Invalidate a stale probe, clear its old result and test the new setup."""
        with self._model_probe_lock:
            if self._closed:
                return False
            self._model_probe_generation += 1
            cancel = self._model_probe_cancel
            if cancel is not None:
                cancel.set()
        gateway = self._gateway
        if gateway is not None:
            gateway.cancel_connection_checks()
            gateway._publish_status(dict(state='unknown', short='模型待验证', message='模型配置已更改。'))
        return self.start_model_auto_check()

    def _run_model_auto_check(self, generation, cancel, retry_network):
        gateway = None
        revision = None
        try:
            with self._model_probe_lock:
                if self._closed or cancel.is_set() or generation != self._model_probe_generation:
                    return
            # Gateway/catalog construction can touch disk; keep it off the UI thread.
            gateway = self.gateway
            if gateway is None or cancel.is_set():
                return
            if not all(callable(getattr(gateway, name, None)) for name in
                       ('model_status', 'get_configuration_snapshot', 'configuration_version',
                        'test_connection', '_publish_status_for_revision')):
                log.debug('automatic model check skipped: gateway has no connection-test interface')
                return
            revision = gateway.configuration_version()
            status = gateway.model_status()
            if status.get('state') == 'ok':
                return  # A genuine model success since this account became ready.
            if status.get('state') == 'unconfigured':
                return  # A new saved preset/key triggers model_configuration_changed.
            if status.get('state') == 'error':
                fault = status.get('fault')
                if not (retry_network and isinstance(fault, dict)
                        and fault.get('category') == 'network'):
                    return
            config, revision = gateway.get_configuration_snapshot()
            if cancel.is_set():
                return
            if not config or not config.get('has_api_key'):
                gateway._publish_status_for_revision(
                    dict(state='unconfigured', short='模型未配置',
                         message='请到设置 → 模型连接选择模型并保存 API Key。',
                         fault=dict(category='key', code='MODEL_KEY_REQUIRED',
                                    message='请到设置 → 模型连接选择模型并保存 API Key。',
                                    short='模型未配置',
                                    actions=[dict(id='settings', label='去设置')])), revision)
                return
            with self._model_probe_lock:
                if self._closed or cancel.is_set() or generation != self._model_probe_generation:
                    return
            if not gateway._publish_status_for_revision(
                    dict(state='checking', short='正在检查模型', message='正在检查模型连接。'), revision):
                return
            gateway.test_connection(cancel=cancel)
        except Exception as exc:  # gateway reports provider/model failures itself
            if gateway is not None and not cancel.is_set():
                from ..connections.errors import ModelError
                if isinstance(exc, ModelError):
                    gateway.report_error(exc, purpose='connection_test',
                                         configuration_revision=revision)
                else:
                    log.warning('automatic model connection check could not start', exc_info=True)
                    gateway._publish_status_for_revision(self._model_probe_failure_status(),
                                                         revision)
            elif gateway is None and not cancel.is_set():
                log.warning('automatic model connection gateway could not start', exc_info=True)
                self._publish_model_probe_failure(generation)
        finally:
            with self._model_probe_lock:
                if self._model_probe_cancel is cancel:
                    self._model_probe_cancel = None
                    self._model_probe_thread = None

    def _publish_model_probe_failure(self, generation):
        value = self._model_probe_failure_status()
        with self._model_probe_lock:
            if self._closed or generation != self._model_probe_generation:
                return
            gateway, listener = self._gateway, self.model_status_listener
        if gateway is not None:
            gateway._publish_status(value)
        elif callable(listener):
            try:
                listener(value)
            except Exception:
                log.warning('model status observer failed')

    @staticmethod
    def _model_probe_failure_status():
        return dict(state='error', short='模型连接检查失败',
                    fault=dict(category='software', code='MODEL_CONFIG_INVALID',
                               message='软件无法启动模型连接检查，请到设置→模型连接重试。',
                               short='模型连接检查失败',
                               actions=[dict(id='settings', label='去设置')]))

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
            if self._model_probe_cancel is not None:
                self._model_probe_cancel.set()
            services, self._gateway, self._store, self._catalog, self._rag = (
                self._rag, self._gateway, self._catalog, self._store), None, None, None, None
            self._chat = None
        for service in services:
            if service is not None:
                try:
                    service.close()
                except Exception:  # noqa: BLE001 - closing one must not stop the others
                    log.warning("service close failed", exc_info=True)
