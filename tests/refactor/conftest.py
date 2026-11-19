"""Whole-run guard: tests must leave the host's permanent environment variables unchanged.

Lesson 2026-11-16 (design-luoling skill): a tool's first run kept appending to the user PATH
until it passed the 32767-character limit and no new process on the machine could start.
"""

import os
from pathlib import Path
import sys

import pytest

_SYSTEM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_BEFORE: dict[str, str] = {}


def pytest_configure(config):
    """Give fixed-artifact browser tests a private directory per pytest worker.

    The tests intentionally keep their evidence files for inspection, but a
    shared absolute directory makes xdist workers delete or overwrite one
    another's screenshots.  ``--basetemp`` is already unique per run, so only
    the worker suffix is needed here.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "gw0")
    raw_base = getattr(config.option, "basetemp", None)
    base = Path(raw_base) if raw_base else Path(r"D:\MentorFlow\.local\test-tmp\pytest-artifacts")
    root = base / f"artifacts-{worker}"
    os.environ["MF_TIMELINE_TEST_ROOT"] = str(root / "timeline")
    os.environ["MODEL199_ARTIFACTS"] = str(root / "model199")
    os.environ["UPDATE258_ARTIFACTS"] = str(root / "banner258")
    os.environ["RAG278_OUTPUT"] = str(root / "rag278")
    os.environ["RAG269_OUTPUT"] = str(root / "rag269")
    os.environ["REPORT_UPGRADE_ROOT"] = str(root / "report-upgrade")


@pytest.fixture(autouse=True)
def restore_process_environment():
    """Keep one worker's process environment from leaking between tests.

    Browser tests need to set Qt/Chromium variables before creating a Qt
    application, but those settings are test-local.  Restoring the mapping at
    the test boundary makes the existing direct assignments safe without
    changing the child-process environments they intentionally construct.
    """
    before = dict(os.environ)
    yield
    for name in list(os.environ):
        if name not in before:
            os.environ.pop(name, None)
    for name, value in before.items():
        if os.environ.get(name) != value:
            os.environ[name] = value


def _host_environment() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    import winreg

    snapshot = {}
    for scope, root, path in (
        ("user", winreg.HKEY_CURRENT_USER, "Environment"),
        ("system", winreg.HKEY_LOCAL_MACHINE, _SYSTEM_KEY),
    ):
        with winreg.OpenKey(root, path) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                snapshot[f"{scope}:{name.upper()}"] = str(value)
                index += 1
    return snapshot


def _changes(before: dict[str, str], after: dict[str, str]) -> list[str]:
    lines = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        added = [p for p in (new or "").split(";") if p and p not in (old or "").split(";")]
        lines.append(f"{name}: {len(old or '')} -> {len(new or '')} chars; added: {'; '.join(added[:5])}")
    return lines


def pytest_sessionstart(session):
    _BEFORE.clear()
    _BEFORE.update(_host_environment())


def pytest_sessionfinish(session, exitstatus):
    changes = _changes(_BEFORE, _host_environment())
    if not changes:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    write = reporter.write_line if reporter else (lambda line: print(line, file=sys.stderr))
    write("HOST ENVIRONMENT CHANGED BY THIS TEST RUN (user/system environment variables):")
    for line in changes:
        write("  " + line)
    session.exitstatus = 1
