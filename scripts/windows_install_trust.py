"""Admin-owned installation receipts. No DPAPI, application imports or data I/O.

The administrator/OS is the trust boundary, not an adversary. Normal startup
never repairs ACLs or creates receipts. Invalid trust requires elevated repair.
"""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
from functools import lru_cache

SCHEMA = "mentorflow.admin-installation.v1"
RECEIPT = "installation-receipt.json"
SDDL = "O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"
TRUSTEES = {"SY", "BA"}


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


def security_descriptor(path: Path) -> str:
    adv, kernel = _api()
    sd = c.c_void_p()
    result = adv.GetNamedSecurityInfoW(str(path), 1, 5, None, None, None, None, c.byref(sd))
    if result:
        raise RepairRequired(f"ACL read failed ({result}): {path}")
    output = w.LPWSTR()
    try:
        if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, 5, c.byref(output), None):
            raise RepairRequired(f"ACL conversion failed: {path}")
        return output.value
    finally:
        if output:
            kernel.LocalFree(output)
        kernel.LocalFree(sd)


def _parts(sddl: str):
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
                bits = {"GR": 0x80000000, "GX": 0x20000000, "GW": 0x40000000, "SD": 0x10000, "RC": 0x20000}
                if "".join(tokens) != fields[2] or any(t not in bits for t in tokens):
                    raise RepairRequired("unsupported ACL rights")
                mask = 0
                for token in tokens:
                    mask |= bits[token]
        aces.append((fields[1], mask, fields[5]))
    if not aces:
        raise RepairRequired("empty or absent DACL")
    return owner, control, aces


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
        if owner not in TRUSTEES and not re.fullmatch(r"S-1-5-21-(?:\d+-){3}500", owner):
            raise RepairRequired(f"ordinary-user-owned installation parent: {parent}")
        for flags, mask, sid in aces:
            # DELETE on a non-volume ancestor also permits renaming that whole
            # ancestor, replacing the path before the native gate can execute.
            unsafe = 0x10000000 | 0x40000 | 0x80000 | 0x40
            if parent != Path(parent.anchor):
                unsafe |= 0x10000
            if "IO" not in flags and sid not in TRUSTEES and mask & unsafe:
                raise RepairRequired(f"ordinary user may replace installation via parent: {parent}")


def _set_protected(path: Path) -> None:
    adv, kernel = _api()
    sd, owner, dacl = c.c_void_p(), c.c_void_p(), c.c_void_p()
    present, defaulted = w.BOOL(), w.BOOL()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(SDDL, 1, c.byref(sd), None):
        raise RepairRequired("cannot create installation ACL")
    try:
        if not adv.GetSecurityDescriptorOwner(sd, c.byref(owner), c.byref(defaulted)) or not adv.GetSecurityDescriptorDacl(sd, c.byref(present), c.byref(dacl), c.byref(defaulted)):
            raise RepairRequired("cannot decode installation ACL")
        code = adv.SetNamedSecurityInfoW(str(path), 1, 0x80000005, owner, None, dacl, None)
        if code:
            raise RepairRequired(f"cannot protect installation ({code}): {path}")
    finally:
        kernel.LocalFree(sd)
    assert_protected(path)


def _root(path: Path) -> Path:
    from scripts import verify_windows_package as v
    root = Path(os.path.abspath(path))
    if len(root.parts) < 2:
        raise RepairRequired("volume root cannot be an installation")
    v._validate_package_root(root)
    assert_safe_parents(root)
    fs = c.create_unicode_buffer(32)
    if not _api()[1].GetVolumeInformationW(root.anchor, None, 0, None, None, None, fs, len(fs)) or fs.value != "NTFS":
        raise RepairRequired("installation volume must be NTFS")
    return root


def _walk(root: Path):
    from scripts import verify_windows_package as v
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                st = path.lstat()
                isdir = stat.S_ISDIR(st.st_mode)
                validation_root = root / "App" if path.is_relative_to(root / "App") and path != root / "App" else root
                v._validate_package_entry(path, validation_root, directory=isdir, cached_stat=st, resolve_path=False)
                yield path, st, isdir
                if isdir:
                    pending.append(path)


def protect_installation(path: Path) -> None:
    """Elevated installer only; never called by normal verification."""
    root = _root(path)
    shell = c.WinDLL("shell32")
    if not shell.IsUserAnAdmin():
        raise RepairRequired("elevated installation/repair required")
    # Validate the entire tree before changing any ACL; never follow aliases.
    entries = list(_walk(root))
    for candidate in [root] + [row[0] for row in reversed(entries)]:
        try:
            assert_protected(candidate)
        except RepairRequired:
            _set_protected(candidate)


def _create_protected(path: Path, *, directory: bool):
    """Atomically create with admin owner/DACL; no user-writable staging window."""
    adv, kernel = _api()
    class SecurityAttributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", c.c_void_p), ("inherit", w.BOOL)]
    sd = c.c_void_p()
    if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(SDDL, 1, c.byref(sd), None):
        raise RepairRequired("cannot create protected staging descriptor")
    attributes = SecurityAttributes(c.sizeof(SecurityAttributes), sd, False)
    try:
        if directory:
            kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, c.POINTER(SecurityAttributes)]
            if not kernel.CreateDirectoryW(str(path), c.byref(attributes)):
                raise RepairRequired(f"cannot create new protected directory ({c.get_last_error()}): {path}")
            return None
        kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        kernel.CreateFileW.restype = w.HANDLE
        handle = kernel.CreateFileW(str(path), 0x40000000, 0, c.byref(attributes), 1, 0x80, None)
        if handle == c.c_void_p(-1).value:
            raise RepairRequired(f"cannot create protected file ({c.get_last_error()}): {path}")
        import msvcrt
        return os.fdopen(msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY), "wb")
    finally:
        kernel.LocalFree(sd)


def install_package(source: Path, destination: Path, **identities):
    """Elevated installer entry; immutable per-version target, never in-place.

    A failed install leaves no valid receipt and is not launched. Existing
    targets are refused rather than silently overwriting an unknown install.
    The enclosing installer supplies visible progress and pins these arguments.
    """
    from scripts import verify_windows_package as v
    if not c.WinDLL("shell32").IsUserAnAdmin():
        raise RepairRequired("installation requires elevation")
    destination = Path(os.path.abspath(destination))
    if len(destination.parts) < 2 or destination.exists():
        raise RepairRequired("new dedicated installation directory required")
    assert_safe_parents(destination)
    source = v._validate_package_root(source).resolve()
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        raise RepairRequired("source and installation must be separate")
    entries = list(v._iter_package_entries(source))
    if v.sha256_file(source / v.MANIFEST_RELATIVE) != identities.get("manifest_sha256"):
        raise RepairRequired("installer manifest anchor mismatch")
    _create_protected(destination, directory=True)
    _root(destination)
    app = destination / "App"
    _create_protected(app, directory=True)
    import shutil
    for path, isdir in sorted(entries, key=lambda row: len(row[0].parts)):
        target = app / path.relative_to(source)
        parent = app
        for part in target.relative_to(app).parts[:-1]:
            parent = parent / part
            if not parent.exists():
                _create_protected(parent, directory=True)
        if isdir:
            _create_protected(target, directory=True)
        else:
            with path.open("rb") as incoming, _create_protected(target, directory=False) as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                outgoing.flush()
                os.fsync(outgoing.fileno())
    # NTFS may deliver a final parent-directory write notification after the
    # newly copied child handles close. Discard that whole verification attempt;
    # never suppress its event or issue a receipt from the interrupted snapshot.
    started = time.perf_counter()
    for attempt in range(3):
        try:
            result = verify_installation(destination, activate=True, **identities)
            result.update(activation_attempts=attempt + 1, activation_total_seconds=time.perf_counter() - started)
            return result
        except v.PackageError as error:
            if str(error) != "package changed during security verification" or attempt == 2:
                raise
            time.sleep(0.1)


def category(relative: str) -> str:
    """Known Qt designer raster icons only. Unknown or behavioral data is code.

    These are display-only PNGs in the shipped Qt designer image directories.
    QML/JS/SVG, story/prompt assets, configs, fonts, plugin metadata, archives
    and all unreviewed paths remain CRITICAL regardless of filename suffix.
    """
    parts = Path(relative).parts
    if len(parts) >= 7 and parts[:3] == ("_internal", "PySide6", "qml") and parts[-3:-1] == ("designer", "images") and Path(relative).suffix == ".png":
        return "STATIC_RESOURCE"
    return "CRITICAL"


def _snapshot(root: Path):
    from scripts import verify_windows_package as v
    assert_protected(root)
    rows, files = {}, {}
    for path, st, isdir in _walk(root):
        assert_protected(path)
        key = path.relative_to(root).as_posix()
        if key == RECEIPT:
            if isdir:
                raise RepairRequired("receipt is not a file")
            continue
        change_time = v._resource_change_time(path) if not isdir and key.startswith("App/") and category(key[4:]) == "STATIC_RESOURCE" else 0
        if not isdir and key.startswith("App/") and category(key[4:]) == "STATIC_RESOURCE" and change_time <= 0:
            raise RepairRequired("static resource ChangeTime unavailable")
        rows[key] = ["dir" if isdir else "file", st.st_size if not isdir else 0, st.st_mtime_ns, st.st_ctime_ns, st.st_dev, st.st_ino, change_time]
        if not isdir:
            if not key.startswith("App/"):
                raise RepairRequired(f"unknown installation control file: {key}")
            files[key[4:]] = path
    if "App" not in rows:
        raise RepairRequired("installed App directory missing")
    # Root time changes when committing the receipt; bind stable identity only.
    st = root.stat()
    return rows, files, [st.st_dev, st.st_ino]


def _identity(rows) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_installation(path: Path, *, manifest_sha256: str, source_sha: str, tree_sha: str, activate: bool = False, workers: int = 4):
    from scripts import verify_windows_package as v
    start = time.perf_counter()
    for name, value, length in [("manifest", manifest_sha256, 64), ("source", source_sha, 40), ("tree", tree_sha, 40)]:
        if not re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value):
            raise RepairRequired(f"invalid pinned {name} identity")
    root = _root(path)
    if activate and not c.WinDLL("shell32").IsUserAnAdmin():
        raise RepairRequired("receipt activation requires elevation")
    with v._watch_gate_directory(root) as unchanged:
        rows, files, root_id = _snapshot(root)
        package = root / "App"
        manifest_path = package / v.MANIFEST_RELATIVE
        if v.sha256_file(manifest_path) != manifest_sha256:
            raise RepairRequired("manifest changed; FULL VERIFY / REPAIR REQUIRED")
        manifest = json.loads(manifest_path.read_bytes())
        if manifest.get("schema") != v.MANIFEST_SCHEMA or manifest.get("product") != "MentorFlow":
            raise RepairRequired("invalid package manifest schema")
        version = v.load_version(package / v.VERSION_FILE_NAME)
        if version["version"] != manifest.get("version"):
            raise RepairRequired("package version mismatch")
        expected = {entry["path"]: entry for entry in manifest["files"]}
        if len(expected) != len(manifest["files"]) or set(files) != set(expected) | {v.MANIFEST_RELATIVE.as_posix()}:
            raise RepairRequired("file set changed; FULL VERIFY / REPAIR REQUIRED")
        required = {"MentorFlow.exe", v.VERSION_FILE_NAME, "SELF-CHECK/MentorFlowSelfCheck.exe", "SELF-CHECK/verify_windows_package.py", "SELF-CHECK/mentorflow-check.bat", "SELF-CHECK/mentorflow-update.bat"}
        if not required.issubset(expected):
            raise RepairRequired("required startup files missing")
        binding = {"schema": SCHEMA, "source_sha": source_sha, "tree_sha": tree_sha, "manifest_sha256": manifest_sha256, "version": manifest["version"], "root": str(root), "root_identity": root_id}
        receipt_path = root / RECEIPT
        if not activate:
            try:
                if receipt_path.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("oversize")
                prior = json.loads(receipt_path.read_bytes())
                if set(prior) != {"binding", "inventory", "file_set_identity", "critical_hashes", "full_verified_at", "full_verified"} or prior["binding"] != binding or prior["inventory"] != rows or prior["file_set_identity"] != _identity(rows) or prior["full_verified"] is not True or type(prior["full_verified_at"]) not in (int, float) or not 0 < prior["full_verified_at"] <= time.time():
                    raise ValueError("invalid receipt")
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise RepairRequired("installation receipt invalid; FULL VERIFY / REPAIR REQUIRED") from error
        keys = sorted(expected) if activate else sorted(key for key in expected if category(key) == "CRITICAL")
        critical, prefixes = {}, {}
        hash_start = time.perf_counter()
        for key, digest, prefix in v._hash_gate_entries(package, keys, workers):
            if digest != expected[key]["sha256"]:
                raise RepairRequired(f"package tampered: {key}; FULL VERIFY / REPAIR REQUIRED")
            prefixes[key] = prefix
            if category(key) == "CRITICAL":
                critical[key] = digest
        hash_seconds = time.perf_counter() - hash_start
        if activate:
            private = v._find_user_data_files(package, validated_files=[files[key] for key in expected], hashed_prefixes=prefixes)
            private += v._find_user_data_files(package, validated_files=[manifest_path])
            if private:
                raise RepairRequired("private data in installation")
        elif prior["critical_hashes"] != critical:
            raise RepairRequired("critical receipt binding mismatch; FULL VERIFY / REPAIR REQUIRED")
        if list((root.stat().st_dev, root.stat().st_ino)) != root_id:
            raise RepairRequired("installation root replaced during verification")
        unchanged()
    if activate:
        receipt = {"binding": binding, "inventory": rows, "file_set_identity": _identity(rows), "critical_hashes": critical, "full_verified_at": time.time(), "full_verified": True}
        # Receipt bytes are admin-owned from their first creation, not secured
        # after a potentially user-owned file has already been opened.
        import uuid
        temporary = root / ("receipt-" + uuid.uuid4().hex + ".pending")
        try:
            with _create_protected(temporary, directory=False) as stream:
                stream.write(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(receipt_path)
        finally:
            temporary.unlink(missing_ok=True)
        assert_protected(receipt_path)
    return {"verification": "PASS", "mode": "INSTALL_FULL" if activate else "FAST", "full_hash": activate, "files_hashed": len(keys), "files_total": len(expected), "hash_seconds": hash_seconds, "total_seconds": time.perf_counter() - start}


def main(argv=None):
    import argparse
    from scripts import verify_windows_package as v
    parser = argparse.ArgumentParser(description="MentorFlow protected installation gate")
    parser.add_argument("command", choices=["protect", "install", "activate", "gate", "launch"])
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--source-sha")
    parser.add_argument("--tree-sha")
    parser.add_argument("--payload-source", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "protect":
            protect_installation(args.install_root)
            print("MENTORFLOW_INSTALL_ACL=PASS")
        else:
            if not all([args.manifest_sha256, args.source_sha, args.tree_sha]):
                raise RepairRequired("pinned release identities required")
            if args.command == "install":
                if args.payload_source is None:
                    raise RepairRequired("installer payload source required")
                result = install_package(args.payload_source, args.install_root, manifest_sha256=args.manifest_sha256, source_sha=args.source_sha, tree_sha=args.tree_sha)
                print(json.dumps(result))
                return 0
            def verify():
                return verify_installation(args.install_root, manifest_sha256=args.manifest_sha256, source_sha=args.source_sha, tree_sha=args.tree_sha, activate=args.command == "activate")
            if args.command == "launch":
                from scripts.windows_installed_launch import launch
                return launch(args.install_root, verify)
            result = verify()
            print(json.dumps(result))
            print("MENTORFLOW_INSTALL_GATE=PASS")
        return 0
    except (RepairRequired, v.PackageError, OSError, ValueError, KeyError, TypeError) as error:
        if args.command == "launch":
            from scripts.windows_installed_launch import show_repair
            show_repair(str(error))
        print(f"FULL VERIFY / REPAIR REQUIRED: {error}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
