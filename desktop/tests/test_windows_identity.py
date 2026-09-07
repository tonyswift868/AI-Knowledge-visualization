import json
import os
from pathlib import Path
import subprocess
import pytest

from desktop import windows_identity as wire

VOLUMES = [0,1,2**53-1,2**53,2**53+1,2**63-1,2**63,2**64-1]


@pytest.mark.parametrize('volume', VOLUMES)
@pytest.mark.parametrize('file_id', [bytes(16), bytes(range(16)), bytes([255])*16])
def test_bit_exact_codec(volume, file_id):
    doc = wire.encode(volume,file_id)
    assert wire.decode(json.loads(json.dumps(doc))) == (volume,file_id)
    assert wire.from_legacy([volume,int.from_bytes(file_id,'little')]) == doc


@pytest.mark.parametrize('change', [dict(volume_hex=2**64-1),dict(volume_hex='F'*16),
    dict(volume_hex='0'*15),dict(volume_hex='0'*17),dict(file_id_le_hex='0'*31),
    dict(file_id_le_hex='g'*32),dict(schema=2),dict(extra=True)])
def test_invalid_wire_rejected(change):
    with pytest.raises(wire.IdentityError): wire.decode(wire.encode(0,bytes(16))|change)


@pytest.mark.parametrize('value', [[True,1],[float(2**63),1],[1,-1],[2**64,1],[1,2**128]])
def test_no_rounding_or_guess_repair(value):
    with pytest.raises(wire.IdentityError): wire.from_legacy(value)


@pytest.mark.skipif(os.name != 'nt',reason='real Windows interop')
def test_actual_native_transport_chain(tmp_path):
    compiler = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
    assert compiler.is_file(), 'configured .NET compiler required for native transport regression'
    native = Path(__file__).resolve().parents[2]/'scripts/native'
    exe = tmp_path/'IdentityWire.exe'
    subprocess.run([str(compiler),'/nologo','/r:System.Web.Extensions.dll','/out:'+str(exe),
                    str(native/'WindowsIdentityWire.cs'),str(native/'TestIdentityWire.cs')],
                   check=True,capture_output=True,timeout=30)
    actual = wire.read_directory(tmp_path)
    st = tmp_path.stat()
    assert actual == wire.from_legacy([st.st_dev,st.st_ino])
    docs = [wire.encode(n,bytes(range(16))) for n in VOLUMES]+[actual]
    payload = json.dumps(docs)
    # Exercise JavaScriptSerializer and a real PowerShell ConvertFrom/To-Json
    # boundary. Strings remain bit-exact; no uint64 enters either Number API.
    native_json = subprocess.run([str(exe)],input=payload,text=True,capture_output=True,
                                 check=True,timeout=10).stdout
    ps = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
        '$v = [Console]::In.ReadToEnd() | ConvertFrom-Json; ConvertTo-Json -InputObject @($v) -Depth 8 -Compress'],
        input=native_json,text=True,capture_output=True,check=True,timeout=15).stdout
    assert json.loads(ps) == docs
    for expected, returned in zip(docs,json.loads(ps)):
        assert wire.decode(returned) == wire.decode(expected)
