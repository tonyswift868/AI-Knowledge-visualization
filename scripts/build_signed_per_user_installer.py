"""Default consumer installer builder; trust keys are read from frozen code.

No execution of embedded Python is needed to inspect literal build authority.
The produced installer's runtime independently verifies the release again.
"""
import argparse
import dis
from pathlib import Path
import subprocess


def literal_authority(code):
    instructions=list(dis.get_instructions(code));result={}
    for index,item in enumerate(instructions):
        if item.opname!='STORE_NAME' or item.argval not in ('APP_ID','PUBLIC_KEYS'):continue
        if item.argval=='APP_ID':
            previous=instructions[index-1]
            if previous.opname!='LOAD_CONST' or not isinstance(previous.argval,str):raise ValueError('nonliteral app identity')
            result['APP_ID']=previous.argval
        else:
            build=instructions[index-1]
            if build.opname!='BUILD_MAP' or not 1<=build.arg<=16:raise ValueError('literal release keys required')
            values=instructions[index-1-2*build.arg:index-1]
            if any(v.opname!='LOAD_CONST' for v in values):raise ValueError('nonliteral release keys')
            result['PUBLIC_KEYS']={values[i].argval:values[i+1].argval for i in range(0,len(values),2)}
    if set(result)!={'APP_ID','PUBLIC_KEYS'}:raise ValueError('compiled release authority missing')
    if any(not isinstance(k,str) or type(v) is not bytes or len(v)!=32 for k,v in result['PUBLIC_KEYS'].items()):
        raise ValueError('invalid compiled release keys')
    return result


def build(payload,signed_release,output,nsis):
    from PyInstaller.archive.readers import CArchiveReader
    from desktop import update_protocol as protocol
    from scripts.per_user_install import verify_tree
    from scripts.install_product import Product
    runtime=Path(payload)/'UserRuntime/UserRuntime.exe'
    archive=CArchiveReader(str(runtime));pyz=archive.open_embedded_archive('PYZ.pyz')
    authority=literal_authority(pyz.extract('mentorflow_delivery_authority'))
    Product(authority['APP_ID'])
    prior=protocol.APP_ID;protocol.APP_ID=authority['APP_ID']
    try:release=protocol.verify_release(Path(signed_release).read_bytes(),authority['PUBLIC_KEYS'],current='0.0.0')
    finally:protocol.APP_ID=prior
    verify_tree(payload,release,subset=list(release.rows))
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    setup=output/f'MentorFlow-{release.version}-PerUser-Setup.exe'
    if setup.exists():raise ValueError('existing installer preserved')
    total=release.signed['full_release']['size']
    source=Path(__file__).resolve().parents[1]/'installer/PerUserInstall.nsi'
    subprocess.run([str(nsis),'/INPUTCHARSET','UTF8','/V3','/DOUTPUT_FILE='+str(setup),'/DUSER_RUNTIME='+str(runtime.parent),
        '/DSIGNED_RELEASE='+str(Path(signed_release).absolute()),'/DAPP_ID='+authority['APP_ID'],
        '/DVERSION='+release.version,'/DSPACE_KIB='+str((total+1023)//1024+65536),
        '/DSPACE_MB='+str((total+1024**2-1)//1024**2+64),str(source)],check=True,timeout=300)
    return setup


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--payload',type=Path,required=True)
    parser.add_argument('--release',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--nsis',type=Path,default=Path('E:/Codex/App/NSIS-3.12/makensis.exe'))
    args=parser.parse_args();print(build(args.payload,args.release,args.output,args.nsis))
