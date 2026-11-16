"""Whole-run guard: tests must leave the host's permanent environment variables unchanged.

Lesson 2026-11-16 (design-luoling skill): a tool's first run kept appending to the user PATH
until it passed the 32767-character limit and no new process on the machine could start.
"""

import sys

_SYSTEM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_BEFORE: dict[str, str] = {}


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
