from pathlib import Path
import time


def pytest_configure(config):
    if not config.option.basetemp:
        station = Path(__file__).resolve().parents[4]
        (station / "temp").mkdir(exist_ok=True)
        config.option.basetemp = str(station / "temp" / ("voice-download-pytest-" + str(time.time_ns())))
