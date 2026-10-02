"""Offline frozen-package startup check, using an explicitly supplied scratch root."""
from pathlib import Path
import json
import traceback


def check(root: Path) -> int:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    result = {"backend_ready": False}
    services = None
    try:
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
