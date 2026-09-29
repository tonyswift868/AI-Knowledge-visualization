"""Runtime contracts extracted from scripts/windows_install_trust.py."""
from __future__ import annotations

import ctypes as c

from ctypes import wintypes as w

import os

from pathlib import Path

import re

import threading

from functools import lru_cache

LEGACY_SCHEMA = "mentorflow.admin-installation.v1"

SCHEMA = "mentorflow.admin-installation.v2"

RECEIPT = "installation-receipt.json"

TRUSTEES = {"SY", "BA"}

PARENT_TRUSTEES = TRUSTEES | {"S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"}

class RepairRequired(RuntimeError):
    """No application code may run; installation full verification is required."""

@lru_cache(maxsize=1)
def _api():
    if os.name != "nt":
        raise RepairRequired("Windows NTFS installation required")
    adv = c.WinDLL("advapi32", use_last_error=True)
    kernel = c.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "GetNamedSecurityInfoW": ([w.LPWSTR, w.DWORD, w.DWORD] + [c.POINTER(c.c_void_p)] * 5, w.DWORD),
        "GetFileSecurityW": ([w.LPCWSTR, w.DWORD, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
        "ConvertSecurityDescriptorToStringSecurityDescriptorW": ([c.c_void_p, w.DWORD, w.DWORD, c.POINTER(w.LPWSTR), c.POINTER(w.DWORD)], w.BOOL),
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": ([w.LPCWSTR, w.DWORD, c.POINTER(c.c_void_p), c.POINTER(w.DWORD)], w.BOOL),
        "GetSecurityDescriptorOwner": ([c.c_void_p, c.POINTER(c.c_void_p), c.POINTER(w.BOOL)], w.BOOL),
        "GetSecurityDescriptorDacl": ([c.c_void_p, c.POINTER(w.BOOL), c.POINTER(c.c_void_p), c.POINTER(w.BOOL)], w.BOOL),
        "SetNamedSecurityInfoW": ([w.LPWSTR, w.DWORD, w.DWORD] + [c.c_void_p] * 4, w.DWORD),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(adv, name)
        fn.argtypes, fn.restype = args, result
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [c.c_void_p], c.c_void_p
    kernel.GetVolumeInformationW.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD, c.POINTER(w.DWORD), c.POINTER(w.DWORD), c.POINTER(w.DWORD), w.LPWSTR, w.DWORD]
    kernel.GetVolumeInformationW.restype = w.BOOL
    return adv, kernel

_acl_buffers = threading.local()

@lru_cache(maxsize=128)
def _descriptor_text(raw: bytes) -> str:
    """Pure conversion of an exact, complete self-relative OS descriptor."""
    adv, kernel = _api()
    sd = c.create_string_buffer(raw)
    output = w.LPWSTR()
    try:
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 5, c.byref(output), None):
            raise RepairRequired("ACL conversion failed")
        return output.value
    finally:
        if output:
            kernel.LocalFree(output)

def security_descriptor(path: Path) -> str:
    # Every call reads the actual owner/DACL from Windows. Only conversion of
    # identical descriptor bytes is cached; never a path's permissions/result.
    # GetFileSecurityW returns a complete self-relative descriptor on success.
    adv, _ = _api()
    buffer = getattr(_acl_buffers, "buffer", None)
    if buffer is None:
        buffer = _acl_buffers.buffer = c.create_string_buffer(4096)
    needed = w.DWORD()
    for attempt in range(2):
        if adv.GetFileSecurityW(str(path), 5, buffer, len(buffer), c.byref(needed)):
            if not 20 <= needed.value <= len(buffer):
                raise RepairRequired(f"ACL descriptor length invalid: {path}")
            return _descriptor_text(c.string_at(buffer, needed.value))
        error = c.get_last_error()
        if attempt or error != 122 or not len(buffer) < needed.value <= 131072:
            raise RepairRequired(f"ACL read failed ({error}): {path}")
        buffer = _acl_buffers.buffer = c.create_string_buffer(needed.value)
    raise RepairRequired(f"ACL read failed: {path}")

@lru_cache(maxsize=128)
def _parts(sddl: str):
    # Cache only pure parsing of an exact descriptor string. Callers must read
    # the actual descriptor from Windows for every path, on every verification.
    match = re.fullmatch(r"O:([^:]+)D:([^()]*)((?:\([^()]+\))*)", sddl)
    if not match:
        raise RepairRequired("unrecognized owner/DACL structure")
    owner, control, raw = match.groups()
    aces = []
    for row in re.findall(r"\(([^()]+)\)", raw):
        fields = row.split(";")
        if len(fields) != 6 or fields[0] != "A" or fields[3] or fields[4]:
            raise RepairRequired("unsupported ACL entry")
        rights = {"FA": 0x1f01ff, "GA": 0x10000000, "GRGX": 0xa0000000, "GXGR": 0xa0000000, "FRFX": 0x1200a9}
        mask = rights.get(fields[2])
        if mask is None:
            if fields[2].startswith("0x"):
                mask = int(fields[2], 16)
            else:
                # Ancestors can use concatenated generic/read/delete rights.
                tokens = re.findall("..", fields[2])
                bits = {"GR": 0x80000000, "GX": 0x20000000, "GW": 0x40000000, "SD": 0x10000, "RC": 0x20000,
                        "LC": 0x4}  # Native SDDL alias present on Windows volume roots.
                if "".join(tokens) != fields[2] or any(t not in bits for t in tokens):
                    raise RepairRequired("unsupported ACL rights")
                mask = 0
                for token in tokens:
                    mask |= bits[token]
        aces.append((fields[1], mask, fields[5]))
    if not aces:
        raise RepairRequired("empty or absent DACL")
    return owner, control, tuple(aces)

def assert_protected(path: Path) -> None:
    owner, control, aces = _parts(security_descriptor(path))
    if owner not in TRUSTEES or "P" not in control:
        raise RepairRequired(f"installation owner/DACL is not protected: {path}")
    effective = {}
    for flags, mask, sid in aces:
        if sid not in {"SY", "BA", "BU"} or "IO" in flags:
            raise RepairRequired(f"untrusted installation ACE: {path}")
        if mask != (0x1200a9 if sid == "BU" else 0x1f01ff):
            raise RepairRequired(f"installation write policy mismatch: {path}")
        effective[sid] = mask
    if set(effective) != {"SY", "BA", "BU"}:
        raise RepairRequired(f"incomplete installation ACL: {path}")

def assert_safe_parents(root: Path) -> None:
    for parent in root.parents:
        st = parent.lstat()
        if st.st_file_attributes & 0x400:
            raise RepairRequired(f"reparse installation parent: {parent}")
        owner, _, aces = _parts(security_descriptor(parent))
        if owner not in PARENT_TRUSTEES and not re.fullmatch(r"S-1-5-21-(?:\d+-){3}500", owner):
            raise RepairRequired(f"ordinary-user-owned installation parent: {parent}")
        for flags, mask, sid in aces:
            # DELETE on a non-volume ancestor also permits renaming that whole
            # ancestor, replacing the path before the native gate can execute.
            unsafe = 0x10000000 | 0x40000 | 0x80000 | 0x40
            if parent != Path(parent.anchor):
                unsafe |= 0x10000
            if "IO" not in flags and sid not in PARENT_TRUSTEES and mask & unsafe:
                raise RepairRequired(f"ordinary user may replace installation via parent: {parent}")

def _check_entry_acls(paths):
    """Fresh per-entry ACL reads; finish all checks before hashing any code."""
    def check_batch(batch):
        for path in batch:
            assert_protected(path)
    if len(paths) < 64:
        check_batch(paths)
        return
    from concurrent.futures import ThreadPoolExecutor
    # Avoid one task per file; worker count is bounded, buffers are thread-local.
    # No successful per-path authorization result survives this verification.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for _ in pool.map(check_batch, (paths[i:i + 64] for i in range(0, len(paths), 64))):
            pass
