"""Lossless Win32 identity wire protocol (no JSON numbers for native IDs).

volume_hex encodes the numeric uint64 VolumeSerialNumber, most significant
hex digit first. file_id_le_hex is bytes(FILE_ID_128.Identifier).hex(), in
native byte-array order, NOT a formatted GUID. Python's st_ino interprets that
array as a little-endian integer; the legacy adapter is explicit and read-only.
"""
import ctypes as c
from ctypes import wintypes as w
import re

SCHEMA = 'mentorflow.win32-file-id.v1'


class IdentityError(ValueError): pass


def encode(volume, file_id):
    if type(volume) is not int or not 0 <= volume < 2**64:
        raise IdentityError('volume must be an exact uint64')
    if type(file_id) is not bytes or len(file_id) != 16:
        raise IdentityError('FileId must be exactly 16 native bytes')
    return dict(schema=SCHEMA, volume_hex=f'{volume:016x}', file_id_le_hex=file_id.hex())


def decode(value):
    if (not isinstance(value, dict) or set(value) != {'schema','volume_hex','file_id_le_hex'}
            or value['schema'] != SCHEMA
            or not isinstance(value['volume_hex'], str)
            or re.fullmatch('[0-9a-f]{16}', value['volume_hex']) is None
            or not isinstance(value['file_id_le_hex'], str)
            or re.fullmatch('[0-9a-f]{32}', value['file_id_le_hex']) is None):
        raise IdentityError('invalid canonical Win32 identity')
    return int(value['volume_hex'], 16), bytes.fromhex(value['file_id_le_hex'])


def from_legacy(value):
    """Only exact Python v1 receipt integers; never round/repair a float."""
    if (type(value) is not list or len(value) != 2
            or type(value[0]) is not int or not 0 <= value[0] < 2**64
            or type(value[1]) is not int or not 0 <= value[1] < 2**128):
        raise IdentityError('invalid exact legacy identity')
    return encode(value[0], value[1].to_bytes(16, 'little'))


def from_handle(kernel, handle):
    class FileIdInfo(c.Structure):
        _fields_ = [('volume',c.c_uint64), ('file_id',c.c_ubyte*16)]
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE,c.c_int,c.c_void_p,w.DWORD]
    info = FileIdInfo()
    if not kernel.GetFileInformationByHandleEx(handle, 18, c.byref(info), c.sizeof(info)):
        raise OSError(c.get_last_error(), 'full Win32 file identity unavailable')
    return encode(info.volume, bytes(info.file_id))


def read_directory(path):
    # Import lazily to keep the pure wire codec dependency-free.
    from desktop.update_tree_security import DirectoryPins, normal
    path = normal(path)
    with DirectoryPins() as pins:
        pins.check(path, path, directory=True, missing=False)
        return from_handle(pins.kernel, pins.handles[path])


def inventory_wire(rows):
    """Receipt v2 inventory: native 64/128-bit fields never enter JSON Number.

    Keep the established seven-column shape. Time fields are uint64 hex in
    their original ns/native units, volume/FileId use the shared wire codec.
    Size stays an integer (the manifest bounds it below 2**53).
    """
    encoded={}
    for key,row in rows.items():
        identity=from_legacy(row[4:6])
        times=[]
        for index in (2,3,6):
            value=row[index]
            if type(value) is not int or not 0 <= value < 2**64:
                raise IdentityError('inventory time outside uint64')
            times.append(f'{value:016x}')
        encoded[key]=[row[0],row[1],times[0],times[1],identity['volume_hex'],identity['file_id_le_hex'],times[2]]
    return encoded
