"""Build real preview binaries; private signing inputs stay in TASK only."""
from pathlib import Path
from types import SimpleNamespace
import argparse, sys, subprocess, shutil, json
from common import ROOT, TASK, APP_ID, FEED, environment
environment(); sys.path.insert(0, str(ROOT))
from scripts import build_learning_installer as b

def freeze(label, version, source):
    b.ROOT = source
    web = TASK / ('web-' + label)
    b.run(['D:/MentorFlow/.local/node/node.exe',
        'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js', 'build',
        '--config', source/'refactor/learning/web/vite.config.mjs', '--outDir', web],
        TASK/'temp', TASK/'logs'/('web-'+label+'.log'))
    original = b.run
    def instrument(command, work, log):
        command = list(command)
        hook = str(Path(__file__).with_name('timing_hook.py'))
        if any(str(p).endswith('MentorFlow.spec') for p in command):
            spec = Path(command[-1]); text = spec.read_text()
            text = text.replace('runtime_hooks=[', 'runtime_hooks=[' + repr(hook) + ', ')
            spec.write_text(text)
        elif '--name' in command and 'UserRuntime' in command:
            command[-1:-1] = ['--runtime-hook', hook]
        original(command, work, log)
    b.run = instrument
    inputs = Path('D:/MentorFlow/.local/release-tools/inputs-2.0.1')
    try:
        b.freeze(SimpleNamespace(work=TASK/('freeze-'+label), public_key=TASK/'test-public.json',
            feed=FEED, app_id=APP_ID, version=version, voice_catalog=inputs/'component-catalog.json',
            seven_zip=inputs/'7zr.exe', public_runtime=TASK/'public-runtime.json',
            account_ca=TASK/'local-api.pem', web_dist=web))
    finally: b.run = original
    return TASK/('freeze-'+label)/'app/MentorFlow'

def stage(label, payload, version, epoch, previous=None, installer=True):
    from refactor.desktop.delivery import update_protocol as p
    p.APP_ID = APP_ID
    prior = b.previous_release
    b.previous_release = lambda *a, **k: SimpleNamespace(version='0.0.0',
        signed={'min_supported_version':'2.0.12','mandatory':False}) if not k.get('path') else prior(*a, **k)
    out = TASK/('release-'+label)
    try:
        b.release(SimpleNamespace(payload=payload, feed=FEED, app_id=APP_ID,
            public_key=TASK/'test-public.json', previous_release=previous, output=out,
            epoch=epoch, valid_days=3, notes='本机预览版验证 '+label, base_versions=[],
            mandatory=False, mandatory_reason='', minimum_version=None))
    finally: b.previous_release = prior
    b.sign(SimpleNamespace(public_key=TASK/'test-public.json',private_key=TASK/'test-private.bin',
        unsigned=out/'unsigned-release.json',output=out/'stable.json'))
    objects=TASK/'webroot/updates/app/objects';objects.mkdir(parents=True,exist_ok=True)
    for file in (out/'objects').iterdir():
        target=objects/file.name
        if not target.exists():
            try: target.hardlink_to(file)
            except OSError: shutil.copy2(file,target)
    if installer:
        b.installer(SimpleNamespace(payload=payload, release=out/'stable.json',
            work=TASK/('inno-'+label), output=TASK/('setup-'+label),
            compiler=Path('E:/Codex/App/InnoSetup-7.1.0/ISCC.exe')))

if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('label',choices=['X','O','Y'])
    parser.add_argument('--source',type=Path,default=ROOT)
    parser.add_argument('--x-payload',type=Path,default=TASK/'freeze-X/app/MentorFlow')
    parser.add_argument('--previous',type=Path,default=TASK/'release-X/stable.json')
    parser.add_argument('--output-label',default=None);args=parser.parse_args()
    if args.label == 'Y':
        label=args.output_label or 'Y';payload=TASK/('payload-'+label)
        if not payload.exists(): shutil.copytree(args.x_payload,payload)
        for f in payload.rglob('mentorflow-version.json'):
            data=json.loads(f.read_bytes());data['version']='9.102.6';f.write_bytes(b.canonical(data))
        if (payload/'SELF-CHECK').exists(): shutil.rmtree(payload/'SELF-CHECK')
        stage(label,payload,'9.102.6',99010262,args.previous,False)
    else:
        version='9.103.14' if args.label=='X' else '2.0.13'
        payload=freeze(args.label,version,args.source)
        # An archived old tree has no .git. Build inputs come from --source;
        # release provenance commands must run in the real checkout.
        b.ROOT=ROOT
        stage(args.label,payload,version,99010261 if args.label=='X' else 99010260)
