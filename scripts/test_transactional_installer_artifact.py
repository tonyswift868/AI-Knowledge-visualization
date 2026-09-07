"""Read-only packaged helper checks, no installer/UAC or application launch."""
import argparse
import configparser
import json
from pathlib import Path
import subprocess
import tempfile
import pefile
from scripts.windows_install_transaction import sha
from scripts import verify_windows_package as v

def pe_manifest(path):
    with pefile.PE(str(path)) as pe:
        items=[pe.get_data(lang.data.struct.OffsetToData,lang.data.struct.Size)
            for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries if entry.id==24
            for item in entry.directory.entries for lang in item.directory.entries]
        return pe.OPTIONAL_HEADER.Subsystem,items

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--helper',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--installer',type=Path)
    args=parser.parse_args(); results={}
    if args.installer:
        subsystem,manifests=pe_manifest(args.installer)
        assert subsystem==2 and any(b'requireAdministrator' in m for m in manifests)
        results['installer']=dict(windowed=True,requireAdministrator=True,sha256=sha(args.installer))
    for name,path in [('helper',args.helper),('main',args.payload/'MentorFlow.exe')]:
        subsystem,manifests=pe_manifest(path)
        assert subsystem==2 and any(b'asInvoker' in m for m in manifests),name
        results[name]=dict(windowed=True,asInvoker=True,sha256=sha(path))
    help_result=subprocess.run([str(args.helper),'--help'],creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
    assert help_result.returncode==0
    ids=json.loads((args.payload/'SELF-CHECK/source-identity.json').read_bytes())
    with tempfile.TemporaryDirectory() as temp:
        status=Path(temp)/'status.ini'
        common=['--payload-source',str(args.payload),'--manifest-sha256',sha(args.payload/v.MANIFEST_RELATIVE),
            '--source-sha',ids['source_sha'],'--tree-sha',ids['tree_sha'],
            '--uninstaller-source',str(args.helper),'--status-file',str(status)]
        for root in ['E:/MentorFlow','E:/MentorFlow-Custom-Preflight','D:/MentorFlow-Custom-Preflight','D:/','E:/']:
            result=subprocess.run([str(args.helper),'preflight','--install-root',root,*common],
                creationflags=subprocess.CREATE_NO_WINDOW,timeout=30)
            ini=configparser.ConfigParser(); ini.read(status,encoding='utf-16')
            expected=3 if root in ('D:/','E:/') else 0
            assert result.returncode==expected,(root,result.returncode,dict(ini['install']))
            results[root]=dict(exit_code=result.returncode,status=dict(ini['install']))
    required=['_internal/PySide6/QtCore.pyd','_internal/PySide6/Qt6Core.dll','_internal/python313.dll']
    for key in required:
        assert (args.payload/key).is_file(),key
        assert (args.payload/key).read_bytes()[:2]==b'MZ',key
    results['qt_python_layout']='PASS_STATIC_PE_PRESENCE_NOT_UI'
    args.report.write_text(json.dumps(results,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False))

if __name__=='__main__': main()
