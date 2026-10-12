"""Verified download reuse across App/coordinator lifetimes (Windows).

The receipt is only an optimization bound to the signed envelope. File identity,
size, write time and NTFS ChangeTime must still match; read handles deny writes
from the final comparison through the directory swap. A missing/changed receipt
falls back to full preparation. No timestamp is changed to manufacture a match.
"""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path

from .update_protocol import SecurityError, canonical, digest, strict_json

RECEIPT = 'prepared-tree.json'


@contextmanager
def inventory(app, release):
    from . import per_user_install as install
    app = install.plain(app)
    handles = []
    kernel = None
    result = {}
    if os.name == 'nt':
        from .update_tree_security import api, Info, normal
        kernel = api()
        class Basic(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ('created', 'accessed', 'written', 'changed')] + [('attributes', ctypes.c_ulong)]
        kernel.GetFileInformationByHandleEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    try:
        for directory, folders, files in os.walk(app, followlinks=False):
            for name in folders:
                install.plain(Path(directory) / name)
            for name in files:
                path = Path(directory) / name
                if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
                    raise SecurityError('prepared tree contains a reparse file')
                if kernel:
                    handle = kernel.CreateFileW(str(path), 0x80000000, 1 | 4, None, 3, 0x00200000, None)
                    if handle == ctypes.c_void_p(-1).value:
                        raise OSError(ctypes.get_last_error(), 'prepared file has a writer or disappeared')
                    handles.append(handle)
                    info, basic = Info(), Basic()
                    final = ctypes.create_unicode_buffer(32768)
                    length = kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
                    if (not length or length >= len(final) or normal(final.value) != normal(path)
                            or not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                            or not kernel.GetFileInformationByHandleEx(handle, 0, ctypes.byref(basic), ctypes.sizeof(basic))
                            or info.attributes & (0x400 | 0x10)):
                        raise SecurityError('prepared file identity changed')
                    value = [info.volume, info.index_high, info.index_low,
                             info.size_high << 32 | info.size_low, basic.written, basic.changed]
                else:
                    stat = path.stat()
                    value = [stat.st_dev, stat.st_ino, 0, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
                result[path.relative_to(app).as_posix()] = value
        if set(result) != set(release.rows) or any(result[n][3] != r['size'] for n, r in release.rows.items()):
            raise SecurityError('prepared inventory differs from signed files')
        yield result
    finally:
        if kernel:
            for handle in reversed(handles):
                kernel.CloseHandle(handle)


def seal(app, release, verify):
    """Exactly one final full SHA256 pass while the files are write-denied."""
    from . import per_user_install as install
    with inventory(app, release) as files:
        verify()
        install.atomic(Path(app).parent / RECEIPT, canonical(dict(
            schema=1, release=digest(release.envelope), files=files)))


@contextmanager
def lease(app, release):
    """Keep verified file objects immutable until rename/activation finishes."""
    from . import per_user_install as install
    try:
        record = strict_json(install.read(Path(app).parent / RECEIPT))
        compatible = record.get('schema') == 1 and record.get('release') == digest(release.envelope)
    except (OSError, ValueError, RuntimeError, AttributeError):
        compatible = False
    if not compatible:
        yield False
        return
    # Do not catch exceptions raised by the caller inside the context manager.
    from contextlib import ExitStack
    with ExitStack() as stack:
        try:
            files = stack.enter_context(inventory(app, release))
            valid = files == record.get('files')
        except (OSError, ValueError, RuntimeError):
            valid = False
        yield valid


def ready(app, release):
    with lease(app, release) as valid:
        return valid
