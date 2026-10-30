from pathlib import Path
import time
import types
import urllib.error
import urllib.parse
import urllib.request

import pytest

from refactor.learning.voice import download as module


@pytest.fixture(autouse=True)
def download_test_opener(monkeypatch):
    """Keep fake-server downloads local even when the host has a proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = types.SimpleNamespace(
        Request=urllib.request.Request,
        urlopen=opener.open,
    )
    local_urllib = types.SimpleNamespace(
        error=urllib.error,
        parse=urllib.parse,
        request=request,
    )
    monkeypatch.setattr(module, "urllib", local_urllib)


def pytest_configure(config):
    if not config.option.basetemp:
        station = Path(__file__).resolve().parents[4]
        (station / "temp").mkdir(exist_ok=True)
        config.option.basetemp = str(station / "temp" / ("voice-download-pytest-" + str(time.time_ns())))
