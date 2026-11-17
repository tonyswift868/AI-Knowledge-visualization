"""Local-only quota contract validation alongside actionlint (never run in CI)."""
from pathlib import Path
import argparse
import subprocess
import yaml


RELEASE_WORKFLOWS=('mf-build.yml','mf-installer.yml','mf-publish.yml')


def check(root):
    # Only release workflows share the quota queue; test workflows (mf-fresh-install) are not checked here.
    paths=[Path(root)/'.github/workflows'/name for name in RELEASE_WORKFLOWS]
    missing=[p.name for p in paths if not p.is_file()]
    if missing:
        raise ValueError('missing release workflows: '+', '.join(missing))
    for path in paths:
        data=yaml.load(path.read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
        if set(data['on'])!={'workflow_dispatch'}:
            raise ValueError('only workflow_dispatch is allowed: '+path.name)
        if data['concurrency']!={'group':'mentorflow-release','queue':'max','cancel-in-progress':'false'}:
            raise ValueError('global queue must not cancel running/pending releases: '+path.name)
        for job in data['jobs'].values():
            maximum=60 if path.stem=='mf-build' else 40
            if not 1<=int(job.get('timeout-minutes','0'))<=maximum or 'strategy' in job:
                raise ValueError('timeout or matrix violates quota contract: '+path.name)
            for step in job['steps']:
                command=step.get('run','')
                if 'pytest' in command or 'npm test' in command:
                    raise ValueError('CI must not run tests: '+path.name)
        print(path.name+': dispatch-only, global queue=max, timeout, no matrix/tests OK')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--actionlint',type=Path)
    args=p.parse_args()
    root=Path(__file__).resolve().parents[2]
    check(root)
    if args.actionlint:
        # actionlint 1.7.12 predates the documented queue property. The strict
        # validation above checks its exact context/value; ignore only this
        # unsupported-field message, never any other actionlint diagnostic.
        subprocess.run([str(args.actionlint),'-ignore',
            'unexpected key "queue" for "concurrency" section',
            *map(str,sorted((root/'.github/workflows').glob('mf-*.yml')))],check=True,cwd=root)
