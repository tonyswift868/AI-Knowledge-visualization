"""Disposable local API process for one verified Desktop account.

No authentication tokens cross this boundary. The GUI keeps the login window;
the process containing module-global SQLite engines is replaced per account.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import threading


def run_account_backend(listener, data_root: str, stop_event) -> None:
    # Rebind after spawn's entry-point imports (including package launch config)
    # and before any backend import. Never consume an inherited database URL.
    root = Path(data_root).resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError("Account data root is unavailable")
    for name in ("MENTORFLOW_DATABASE_URL", "MENTORFLOW_MIGRATION_DATABASE_URL"):
        os.environ.pop(name, None)
    os.environ.update({
        "MENTORFLOW_DEPLOYMENT_MODE": "PERSONAL_LOCAL",
        "MENTORFLOW_USER_DATA_ROOT": str(root),
        "MENTORFLOW_DATA_DIR": str(root / "data"),
        "MENTORFLOW_DB": str(root / "data" / "app.db"),
        "MENTORFLOW_ORM_DB": str(root / "data" / "mentorflow_v2.db"),
        "MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE": "1",
    })
    from .main import _bind_local_teacher_asset_vault
    _bind_local_teacher_asset_vault(root)
    backend_root = Path(__file__).resolve().parents[1] / "backend"
    if backend_root.is_dir() and str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    import uvicorn
    from app.main import app

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=listener.getsockname()[1],
        access_log=False, log_level="warning", log_config=None,
    ))

    def watch_stop():
        import multiprocessing
        parent = multiprocessing.parent_process()
        while not stop_event.wait(.25):
            if parent is not None and not parent.is_alive():
                break
        server.should_exit = True

    threading.Thread(target=watch_stop, name="account-api-stop", daemon=True).start()
    try:
        if not stop_event.is_set():
            server.run(sockets=[listener])
    finally:
        listener.close()
