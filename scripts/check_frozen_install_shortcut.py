"""Synthetic frozen installer check. Never executes the installed fixture."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import uuid

from scripts import windows_install_shortcut as link
from scripts import windows_install_trust as trust
from scripts import verify_windows_package as v
from scripts.test_native_install_shortcut import NativeShortcutTests
from scripts.test_windows_install_trust import TEST_ROOT


def read_link(path):
    ole = c.WinDLL('ole32')
    link._check(ole.CoInitializeEx(None, 2))
    obj, persist = c.c_void_p(), c.c_void_p()
    try:
        clsid = link.Guid.parse('00021401-0000-0000-c000-000000000046')
        iid = link.Guid.parse('000214f9-0000-0000-c000-000000000046')
        link._check(ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid), c.byref(obj)))
        iid_file = link.Guid.parse('0000010b-0000-0000-c000-000000000046')
        link._check(link._method(obj, 0, c.POINTER(link.Guid), c.POINTER(c.c_void_p))(obj, c.byref(iid_file), c.byref(persist)))
        link._check(link._method(persist, 5, w.LPCWSTR, w.DWORD)(persist, str(path), 0))
        target, arguments = c.create_unicode_buffer(4096), c.create_unicode_buffer(4096)
        link._check(link._method(obj, 3, w.LPWSTR, c.c_int, c.c_void_p, w.DWORD)(obj, target, len(target), None, 4))
        link._check(link._method(obj, 10, w.LPWSTR, c.c_int)(obj, arguments, len(arguments)))
        return target.value, arguments.value
    finally:
        if persist:
            link._method(persist, 2)(persist)
        if obj:
            link._method(obj, 2)(obj)
        ole.CoUninitialize()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--helper', type=Path, required=True)
    args = parser.parse_args()
    trust.assert_safe_parents(args.helper)
    trust.assert_protected(args.helper)
    fixture = NativeShortcutTests()
    fixture.setUp()
    destination = TEST_ROOT / ('frozen-installed-' + uuid.uuid4().hex)

    def install(root, shortcut):
        command = [str(args.helper), 'install', '--install-root', str(root),
                   '--payload-source', str(fixture.root / 'App'),
                   '--shortcut-path', str(shortcut), '--quiet-install']
        for key, value in fixture.fixture.kw.items():
            command.extend(['--' + key.replace('_', '-'), value])
        return subprocess.run(command, creationflags=subprocess.CREATE_NO_WINDOW, timeout=60).returncode

    if install(destination, fixture.destination) != 0:
        raise RuntimeError('Frozen install failed')
    result = trust.verify_installation(destination, **fixture.fixture.kw)
    assert result['mode'] == 'FAST'
    trust.assert_protected(fixture.destination)
    assert fixture.destination.stat().st_nlink == 1
    target, arguments = read_link(fixture.destination)
    assert Path(target) == destination / 'App/MentorFlowInstallGate.exe'
    assert arguments.startswith('launch --install-root ')
    assert all(value in arguments for value in fixture.fixture.kw.values())
    assert 'powershell' not in arguments.lower()

    receipt_hash = v.sha256_file(destination / trust.RECEIPT)
    link_hash = v.sha256_file(fixture.destination)
    assert install(destination, fixture.destination) == 3
    assert v.sha256_file(destination / trust.RECEIPT) == receipt_hash
    assert v.sha256_file(fixture.destination) == link_hash

    bad_root = TEST_ROOT / ('frozen-rejected-' + uuid.uuid4().hex)
    bad_link = fixture.desktop / 'Rejected.lnk'
    (fixture.root / 'App/Qt6Core.dll').write_bytes(b'tampered isolated fixture')
    assert install(bad_root, bad_link) == 3
    assert not (bad_root / trust.RECEIPT).exists()
    assert not bad_link.exists()

    report = dict(helper=str(args.helper), helper_sha256=v.sha256_file(args.helper),
                  installed_root=str(destination), shortcut=str(fixture.destination),
                  target=target, arguments=arguments, verification=result,
                  frozen_install_pass=True, existing_entries_preserved=True,
                  tampered_payload_rejected=True, fixture_executed=False,
                  outer_bootstrap_qualified=False)
    # Generated diagnostics stay in the new isolated protected fixture directory.
    output = fixture.desktop / 'frozen-install-result.json'
    with trust._create_protected(output, directory=False) as stream:
        stream.write(json.dumps(report, indent=2).encode('utf-8'))
    print(json.dumps(dict(report=report, evidence=str(output))), flush=True)


if __name__ == '__main__':
    main()
