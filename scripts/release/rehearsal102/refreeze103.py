"""Refresh cached PyInstaller builds after a UAT hook/web-resource correction."""
import argparse,json,shutil,sys
from common import TASK,ROOT,environment
environment();sys.path.insert(0,str(ROOT))
from build import b,stage
parser=argparse.ArgumentParser();parser.add_argument('label',choices=['O','X']);args=parser.parse_args()
work=TASK/('freeze-'+args.label)
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
stage('O' if args.label=='O' else 'N',payload,'2.0.13' if args.label=='O' else '9.103.14',
      99010260 if args.label=='O' else 99010314,installer=args.label=='O')
print('verified local-only configuration and refreshed hook:',args.label)
