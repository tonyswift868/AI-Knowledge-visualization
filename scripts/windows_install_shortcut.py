"""Elevated native shortcut publication after the protected installation gate.

Uses system COM interfaces only; no PowerShell or third-party COM dependency.
Never overwrites an existing entry or starts the product from the installer.
"""
import ctypes as c
from ctypes import wintypes as w
import os
from pathlib import Path
import uuid


class Guid(c.Structure):
    _fields_ = [('bytes', c.c_ubyte * 16)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _method(pointer, index, *arguments):
    table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
    return c.WINFUNCTYPE(c.c_long, c.c_void_p, *arguments)(table[index])


def _check(code):
    if code < 0:
        raise OSError(f'Shell link COM failure: 0x{code & 0xffffffff:08x}')


def _save_link(path, target, arguments, directory, icon):
    ole = c.WinDLL('ole32', use_last_error=True)
    ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
    ole.CoInitializeEx.restype = c.c_long
    ole.CoCreateInstance.argtypes = [c.POINTER(Guid), c.c_void_p, w.DWORD, c.POINTER(Guid), c.POINTER(c.c_void_p)]
    ole.CoCreateInstance.restype = c.c_long
    _check(ole.CoInitializeEx(None, 2))
    link, persist = c.c_void_p(), c.c_void_p()
    try:
        clsid = Guid.parse('00021401-0000-0000-c000-000000000046')
        iid = Guid.parse('000214f9-0000-0000-c000-000000000046')
        _check(ole.CoCreateInstance(c.byref(clsid), None, 1, c.byref(iid), c.byref(link)))
        # IShellLinkW vtable: SetDescription, SetWorkingDirectory, SetArguments,
        # SetShowCmd, SetIconLocation and SetPath. HRESULT checked every time.
        for index, value in ((7, 'MentorFlow'), (9, str(directory)), (11, arguments), (20, str(target))):
            _check(_method(link, index, w.LPCWSTR)(link, value))
        _check(_method(link, 15, c.c_int)(link, 1))
        _check(_method(link, 17, w.LPCWSTR, c.c_int)(link, str(icon), 0))
        iid_persist = Guid.parse('0000010b-0000-0000-c000-000000000046')
        _check(_method(link, 0, c.POINTER(Guid), c.POINTER(c.c_void_p))(link, c.byref(iid_persist), c.byref(persist)))
        _check(_method(persist, 6, w.LPCWSTR, w.BOOL)(persist, str(path), True))
    finally:
        if persist:
            _method(persist, 2)(persist)
        if link:
            _method(link, 2)(link)
        ole.CoUninitialize()


def publish_shortcut(root: Path, destination: Path, **identities):
    from scripts import windows_install_trust as trust
    from scripts import verify_windows_package as v
    if not c.WinDLL('shell32').IsUserAnAdmin():
        raise trust.RepairRequired('shortcut publication requires elevation')
    root = trust._root(root)
    destination = Path(os.path.abspath(destination))
    if destination.suffix.lower() != '.lnk' or destination.is_relative_to(root):
        raise trust.RepairRequired('shortcut must be an external .lnk file')
    v._validate_existing_parent_chain(destination, label='installer shortcut')
    trust.assert_safe_parents(destination)
    trust._root(destination.parent)  # dedicated existing directory on NTFS
    if destination.exists():
        raise trust.RepairRequired('existing shortcut is preserved; choose a new entry')
    trust.verify_installation(root, **identities)
    gate = root / 'App/MentorFlowInstallGate.exe'
    if not gate.is_file():
        raise trust.RepairRequired('integrated native launch gate is missing')
    arguments = ('launch --install-root "' + str(root) + '" --manifest-sha256 ' + identities['manifest_sha256']
                 + ' --source-sha ' + identities['source_sha'] + ' --tree-sha ' + identities['tree_sha'])
    if (root / 'App/SELF-CHECK/gui-bootstrap-closure.json').is_file():
        # Only a fully verified GUI-enabled installation selects this route.
        # The windowed EXE owns one LoginGate across both startup barriers.
        gate = root / 'App/MentorFlow.exe'
        arguments = arguments.removeprefix('launch ')
    temporary = destination.parent / ('e107-' + uuid.uuid4().hex + '.lnk')
    try:
        with trust._create_protected(temporary, directory=False):
            pass
        _save_link(temporary, gate, arguments, root / 'App', root / 'App/MentorFlow.exe')
        trust._set_protected(temporary)
        # Atomic no-replace publication, including a concurrently created entry.
        os.link(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    trust.assert_protected(destination)
    return str(destination)
