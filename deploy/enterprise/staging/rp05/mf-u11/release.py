"""MF-U11, print-only by default. Only an authorized operator adds --execute."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import time
from package_ops import BASE,IMAGE,KC_IMAGE,compose,kc_compose,bundle,capture,configuration


def image_check(mode, package):
    return ['docker','run','--rm','--network=none','--read-only','--tmpfs','/tmp',
        '--mount','type=bind,src='+str(package.resolve())+',dst=/bundle,readonly','--entrypoint','python',
        BASE if mode=='base' else IMAGE,'/bundle/image_check.py',mode,'--execute']


def identity_check(package):
    return ['docker','run','--rm','--network=none','--read-only','--tmpfs','/tmp',
        '--mount','type=bind,src='+str(package.resolve())+',dst=/bundle,readonly','--entrypoint','java',KC_IMAGE,
        '-cp','/opt/keycloak/lib/lib/main/*:/opt/keycloak/providers/*:/bundle/link-check.jar',
        'win.clannad.mentorflow.check.VerifierLinkCheck']


def commands(action,args,package):
    def phase(name):
        return [sys.executable,str(package/'release.py'),name,'--env-file',str(args.env_file),
            '--keycloak-env-file',str(args.keycloak_env_file),'--state-dir',str(args.state_dir),'--origin',args.origin,'--ca-file',str(args.ca_file),
            '--token-file',str(args.token_file),'--execute']
    def policy(name):
        return [sys.executable,str(package/'realm_admin.py'),name,'--origin',args.origin,
            '--ca-file',str(args.ca_file),'--token-file',str(args.token_file),
            '--backup',str(args.state_dir/'update-password.json'),'--execute']
    def up(service):
        return (kc_compose if service=='keycloak' else compose)(args.keycloak_env_file if service=='keycloak' else args.env_file)+['up','-d','--no-deps','--no-build','--pull','never','--force-recreate',service]
    if action=='prepare':return [phase('bundle'),image_check('base',package),phase('capture'),policy('capture')]
    if action=='build':return [phase('bundle'),phase('check'),image_check('base',package),
        ['docker','build','--network=none','--pull=false','-t',IMAGE,'-f',str(package/'Dockerfile'),str(package)],
        image_check('candidate',package),
        ['docker','build','--network=none','--pull=false','-t',KC_IMAGE,'-f',str(package/'Dockerfile.keycloak'),str(package)],identity_check(package)]
    if action=='deploy':return [phase('bundle'),phase('check'),image_check('base',package),image_check('candidate',package),identity_check(package),policy('check'),
        compose(args.env_file)+['stop','backend'],kc_compose(args.keycloak_env_file)+['stop','keycloak'],phase('patch'),
        up('keycloak'),phase('healthy-keycloak'),policy('apply'),up('backend'),phase('healthy-backend')]
    if action=='rollback':return [phase('check'),compose(args.env_file)+['stop','backend'],kc_compose(args.keycloak_env_file)+['stop','keycloak'],
        phase('restore'),up('keycloak'),phase('healthy-keycloak'),policy('rollback'),up('backend'),phase('healthy-backend')]
    raise ValueError('unknown action')


def wait_healthy(service,env,*,run=subprocess.run,clock=time.monotonic,sleep=time.sleep,timeout=90):
    deadline=clock()+timeout;command=kc_compose if service=='keycloak' else compose
    def query(argv):
        remaining=deadline-clock()
        if remaining<=0:raise TimeoutError(service+' health deadline exceeded')
        return run(argv,check=True,capture_output=True,text=True,timeout=min(5,remaining)).stdout.strip()
    while clock()<deadline:
        ids=query(command(env)+['ps','-a','-q',service]).splitlines()
        if len(ids)>1:raise RuntimeError('expected one '+service+' container')
        if ids:
            state=query(['docker','inspect','--format','{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}',ids[0]])
            if state=='running healthy':return
            if state.split()[0] in ('exited','restarting','dead') or state.endswith(' missing'):raise RuntimeError(service+' is not healthy: '+state)
        sleep(min(2,max(0,deadline-clock())))
    raise TimeoutError(service+' did not become healthy within 90 seconds')


def operate(action,args,package,*,run=subprocess.run):
    touched=False
    deferred=None
    try:
        for command in commands(action,args,package):
            print(shlex.join(command),flush=True)
            if action=='deploy' and command[-2:]==['stop','backend']:touched=True
            try:
                run(command,check=True)
            except Exception as failure:
                if action=='rollback' and str(package/'realm_admin.py') in command:
                    # Restore service availability even if the administrator's
                    # token expired. Still report the incomplete realm rollback.
                    deferred=failure
                    print('Realm rollback failed; continuing to restore and check MF-U10 backend.',flush=True)
                else:raise
        if deferred is not None:raise RuntimeError('Services restored; realm policy rollback still requires attention') from deferred
    except Exception as failure:
        if action=='deploy' and touched:
            print('MF-U11 failed; automatically restoring MF-U10 + original Keycloak + captured realm action; DB stays 0042.',flush=True)
            try:operate('rollback',args,package,run=run)
            except Exception as rollback:raise RuntimeError('MF-U11 and rollback failed; keep incident open') from rollback
            raise RuntimeError('MF-U11 failed; rollback is healthy') from failure
        raise


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','build','deploy','rollback','bundle','capture','check','patch','restore','healthy-backend','healthy-keycloak'])
    p.add_argument('--keycloak-env-file',type=Path,required=True);p.add_argument('--env-file',type=Path,required=True);p.add_argument('--state-dir',type=Path,required=True)
    p.add_argument('--origin',required=True);p.add_argument('--ca-file',type=Path,required=True);p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--execute',action='store_true');args=p.parse_args(argv);package=Path(__file__).resolve().parent
    public=args.action in ('prepare','build','deploy','rollback')
    if not args.execute:
        if public:
            for command in commands(args.action,args,package):print(shlex.join(command))
        print('DRY RUN: '+args.action+'; no command executed. Health deadline 90 seconds/service; deployment failure triggers rollback; schema unchanged.')
        return
    if public:operate(args.action,args,package)
    elif args.action=='bundle':bundle(package)
    elif args.action=='capture':capture(args.env_file,args.keycloak_env_file,args.state_dir,package)
    elif args.action.startswith('healthy-'):wait_healthy(args.action.removeprefix('healthy-'),args.keycloak_env_file if args.action=='healthy-keycloak' else args.env_file)
    else:configuration(args.action,args.env_file,args.keycloak_env_file,args.state_dir)


if __name__=='__main__':main()
