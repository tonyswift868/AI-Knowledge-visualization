"""Refresh cached PyInstaller builds after a UAT hook/web-resource correction."""
import argparse,json,shutil,sys
from common import TASK,ROOT,APP_ID,environment
environment();sys.path.insert(0,str(ROOT))
from build import b,stage
parser=argparse.ArgumentParser();parser.add_argument('label',choices=['O','X','X-final','X-final3','X-verified','X-persist','X-release160','X-release160b','P','Q'])
parser.add_argument('--check-only',action='store_true')
parser.add_argument('--source-root',type=__import__('pathlib').Path,help='Also compare frozen update code with this final source tree')
args=parser.parse_args()
work=TASK/('freeze-X-final' if args.label=='X-final3' else 'freeze-'+args.label)
payload=work/'app/MentorFlow'
def check():
    config=json.loads((payload/'_internal/refactor/desktop/resources/public-runtime.json').read_bytes())
    assert config['MENTORFLOW_ENTERPRISE_API_BASE_URL']=='https://127.0.0.1:18764'
    assert config['MENTORFLOW_DESKTOP_OIDC_ISSUER']=='https://localhost:18764/test-realm'
    from PyInstaller.archive.readers import CArchiveReader
    for exe in (payload/'MentorFlow.exe',payload/'UserRuntime/UserRuntime.exe'):
        archive=CArchiveReader(str(exe))
        hook=archive.extract('timing_hook')
        assert b'UAT_LOCAL_ONLY' in hook and b'127.0.0.1' in hook,exe
        if args.source_root:
            if exe.name=='UserRuntime.exe':
                import marshal
                entry=marshal.loads(archive.extract('per_user_install'))
                path=args.source_root/'refactor/desktop/delivery/per_user_install.py'
                assert entry==compile(path.read_bytes(),entry.co_filename,'exec',dont_inherit=True,optimize=0),'stale frozen entry script'
            pyz=archive.open_embedded_archive('PYZ.pyz')
            names=['refactor.desktop.delivery.per_user_install',
                   'refactor.desktop.delivery.per_user_update_core',
                   'refactor.desktop.delivery.update_download',
                   'refactor.desktop.delivery.update_protocol',
                   'refactor.desktop.delivery.prepared_update',
                   'refactor.desktop.delivery.update_service']
            if exe.name=='MentorFlow.exe':
                names.extend(['refactor.desktop.updates',
                              'refactor.desktop.update_dialog',
                              'refactor.desktop.account_shell_main',
                              'refactor.learning.app.handlers'])
            for name in names:
                code=pyz.extract(name)
                source=args.source_root/(name.replace('.', '/')+'.py')
                expected=compile(source.read_bytes(),code.co_filename,'exec',dont_inherit=True,optimize=0)
                assert code==expected,('frozen code is not final source',exe,name)
            authority=pyz.extract('mentorflow_delivery_authority')
            assert APP_ID in authority.co_consts, 'wrong frozen app identity'
            key_id=json.loads((TASK/'test-public.json').read_bytes())['key_id']
            assert repr(key_id) in repr(authority.co_consts), 'wrong frozen preview key identity'
    print('verified local-only configuration and frozen hook:',args.label)
if args.check_only:
    check();sys.exit(0)
shutil.copyfile(TASK/'public-runtime.json',work/'resources/public-runtime.json')
shutil.copyfile(TASK/'local-api.pem',work/'resources/trust/account-api.pem')
if args.label=='X':shutil.copytree(TASK/'web-final',TASK/'web-X',dirs_exist_ok=True)
b.run([sys.executable,'-B','-m','PyInstaller','--noconfirm','--distpath',work/'app',
       '--workpath',work/'app-build',work/'MentorFlow.spec'],work,work/'app-refresh.log')
payload=work/'app/MentorFlow'
shutil.copytree(work/'runtime/UserRuntime',payload/'UserRuntime',dirs_exist_ok=True)
shutil.copyfile(work/'mentorflow-version.json',payload/'mentorflow-version.json')
config=json.loads((payload/'_internal/refactor/desktop/resources/public-runtime.json').read_bytes())
assert config['MENTORFLOW_ENTERPRISE_API_BASE_URL']=='https://127.0.0.1:18764'
assert config['MENTORFLOW_DESKTOP_OIDC_ISSUER']=='https://localhost:18764/test-realm'
stage('O' if args.label=='O' else 'N',payload,'2.0.13' if args.label=='O' else '9.104.14',
      99010260 if args.label=='O' else 99010314,installer=args.label=='O')
print('verified local-only configuration and refreshed hook:',args.label)
