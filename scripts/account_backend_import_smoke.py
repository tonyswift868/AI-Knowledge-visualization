"""Source/frozen fixture gate; invoke with a NEW disposable output directory.

python -m scripts.account_backend_import_smoke E:/Codex/Temp/new-fixture
The same module is the entry of the diagnostic PyInstaller build. No credentials
or existing account directories are accepted or required.
"""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
import socket
import sys
import threading
import time


def _child(listener, root, stop):
    from desktop.account_backend import run_account_backend
    before = list(sys.path)

    def observe():
        while not stop.wait(.05):
            module = sys.modules.get("app.main")
            if module is not None and hasattr(module, "app"):
                from desktop.backend_imports import prepare_backend_imports
                prepare_backend_imports()
                assert sys.path == before, "Backend startup mutated search paths"
                spec = module.__spec__
                Path(root, "origin.json").write_text(json.dumps({
                    "module": module.__name__, "origin": spec.origin,
                    "loader": type(spec.loader).__name__,
                    "pyz_entry": getattr(spec.loader, "_pyz_entry_name", None),
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "search_path_unchanged": sys.path == before,
                }), encoding="utf-8")
                return

    threading.Thread(target=observe, daemon=True).start()
    run_account_backend(listener, root, stop)


def main():
    import httpx
    root = Path(sys.argv[1]).resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "data").mkdir()
    os.environ["MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY"] = "1"
    os.environ["MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE"] = "1"
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    context = multiprocessing.get_context("spawn")
    stop = context.Event()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    address = f"http://127.0.0.1:{listener.getsockname()[1]}"
    child = context.Process(target=_child, args=(listener, str(root), stop))
    child.start()
    try:
        deadline = time.monotonic() + 90
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                assert child.is_alive(), f"Backend exited: {child.exitcode}"
                try:
                    response = client.get(address + "/api/health")
                    if response.status_code == 200 and response.json().get("service") == "MentorFlow" and (root / "origin.json").is_file():
                        result = json.loads((root / "origin.json").read_text(encoding="utf-8"))
                        result["health"] = response.json()
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError("Backend startup timed out")
    finally:
        # A failed windowed child may have exited while holding its Event's
        # lock; do not touch that synchronization primitive after child death.
        if child.is_alive():
            stop.set()
        child.join(15)
        if child.is_alive():
            child.terminate()
            child.join(10)
        listener.close()
    assert child.exitcode == 0, f"Backend did not stop cleanly: {child.exitcode}"
    result["exitcode"] = child.exitcode
    (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
