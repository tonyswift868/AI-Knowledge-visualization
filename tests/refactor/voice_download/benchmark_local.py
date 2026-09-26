"""Opt-in real TTS model download; never collected by pytest.

Run only with permission to download ~1.54 GB into the supplied data root.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from refactor.learning.voice.tts.model_manager import ModelManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    manager = ModelManager(args.data_root)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "before": manager.status()}
    start, last = time.perf_counter(), 0
    def progress(done, total):
        nonlocal last
        elapsed = time.perf_counter() - start
        if elapsed - last >= 10 or done == total:
            print(f"{done}/{total} bytes; {elapsed:.2f}s", flush=True)
            last = elapsed
    try:
        manager.download(progress, threading.Event())
        report["after"] = manager.status()
    except Exception as exc:
        report["error"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - start
        report["actual_urls"] = manager.last_download_urls
        report["resolved_urls_without_query"] = {
            name: urlunsplit(urlsplit(url)._replace(query="")) for name, url in manager.last_resolved_urls.items()}
        args.data_root.mkdir(parents=True, exist_ok=True)
        (args.data_root / "download-measurements.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
