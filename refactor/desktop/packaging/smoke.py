"""Offline frozen-package startup check, using an explicitly supplied scratch root."""
from pathlib import Path
import json
import traceback


def loaded_sqlite_path() -> Path:
    """Report the DLL actually loaded by _sqlite3, not a search-path guess."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel.GetModuleHandleW.restype = wintypes.HMODULE
    kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetModuleFileNameW.restype = wintypes.DWORD
    module = kernel.GetModuleHandleW('sqlite3.dll')
    if not module:
        raise RuntimeError('sqlite3.dll was not loaded')
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(module, buffer, len(buffer))
    if not length or length >= len(buffer):
        raise RuntimeError('Cannot resolve the loaded sqlite3.dll')
    return Path(buffer.value).resolve()


def sqlite_runtime_info(payload: Path) -> dict:
    import hashlib
    import sqlite3
    lock = json.loads((payload / '_internal/sqlite-runtime.json').read_text(encoding='utf-8'))
    with sqlite3.connect(':memory:') as db:
        version, source_id = db.execute('SELECT sqlite_version(), sqlite_source_id()').fetchone()
    dll = loaded_sqlite_path()
    if dll != (payload / '_internal/sqlite3.dll').resolve():
        raise RuntimeError('SQLite loaded outside the frozen payload: ' + str(dll))
    with dll.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != lock['sha256']:
            raise RuntimeError('Frozen SQLite DLL SHA256 mismatch')
    if sqlite3.sqlite_version != lock['version'] or version != lock['version'] or not source_id:
        raise RuntimeError('Unexpected frozen SQLite runtime: ' + repr((sqlite3.sqlite_version, version, source_id)))
    return dict(version=version, source_id=source_id, loaded_dll=str(dll))


def check(root: Path) -> int:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    result = {"backend_ready": False}
    services = None
    try:
        import sys
        if getattr(sys, 'frozen', False):
            result['sqlite_runtime'] = sqlite_runtime_info(Path(sys.executable).resolve().parent)
        from PySide6.QtWidgets import QApplication
        from refactor.learning.app.services import AppServices
        from refactor.learning.course.catalog_client import _DATA
        ca = Path(__file__).resolve().parents[1] / "resources/trust/account-api.pem"
        app = QApplication.instance() or QApplication([])
        services = AppServices(account_id="mfa1_" + "0" * 64, data_root=root / "data",
            session_provider=lambda: None, api_base_url="https://example.invalid",
            ca_bundle=ca)
        catalog = services.catalog
        lists, prices = catalog.load_lists(), catalog.load_prices()
        # Exercise the real account-local backend, but never credentials/models.
        store = services.store
        result.update(backend_ready=True, catalog_data=str(_DATA),
            source_lists_read=(_DATA / "source_lists.json").is_file(),
            model_prices_read=(_DATA / "model-prices.json").is_file(),
            source_lists_type=type(lists).__name__, prices_keys=sorted(prices),
            store_type=type(store).__name__)
        app.processEvents()
        return 0
    except Exception:
        result["error"] = traceback.format_exc()
        return 1
    finally:
        if services is not None:
            services.close()
        (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
