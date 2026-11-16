"""Small SSH/SCP adapter; LocalServer uses exactly the same filesystem operations."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from scripts.release import server_ops

HOST = 'mentorflow-admin@89.45.45.28'
KNOWN_HOST = '89.45.45.28 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICJMlf3cA3DHteuBGx2K1IR+oJbkLSsIjKo4UXC8z5oZ'


class LocalServer:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.app, self.state = self.root / 'app', self.root / 'state'
        self.state.mkdir(parents=True, exist_ok=True)

    def call(self, action, version, **values):
        return server_ops.dispatch(dict(action=action, version=version, app=str(self.app),
                                        state=str(self.state), **values))

    def upload(self, source):
        target = self.state / ('transfer-' + uuid.uuid4().hex)
        shutil.copyfile(source, target)
        return str(target)

    def download(self, relative, target):
        source = self.app / relative
        target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target


class SSHServer:
    def __init__(self, work, *, identity=None):
        self.work = Path(work).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        hosts = self.work / 'known_hosts'
        public_host = os.environ.get('MF_SSH_KNOWN_HOSTS', KNOWN_HOST)
        hosts.write_text(public_host.strip() + '\n', encoding='ascii')
        self.options = ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                        '-o', 'UserKnownHostsFile=' + str(hosts), '-o', 'ConnectTimeout=20']
        if identity is not None:
            key = Path(identity)
        elif os.environ.get('MF_SSH_KEY'):
            key = self.work / 'ssh-key'
            key.write_text(os.environ['MF_SSH_KEY'].strip() + '\n', encoding='ascii')
            if os.name == 'nt':
                who = subprocess.check_output(['whoami'], text=True).strip()
                subprocess.run(['icacls', str(key), '/inheritance:r', '/grant:r', who + ':F'],
                               check=True, stdout=subprocess.DEVNULL)
            else:
                key.chmod(0o600)
        else:
            raise ValueError('set MF_SSH_KEY or pass --ssh-identity; local rehearsal uses --local-server-root')
        self.options += ['-i', str(key), '-o', 'IdentitiesOnly=yes']

    def call(self, action, version, **values):
        request = dict(action=action, version=version,
                       app='/opt/mentorflow/update-static/e108/app', state='/opt/mentorflow/state', **values)
        source = Path(server_ops.__file__).read_text(encoding='utf-8')
        # Windows OpenSSH reconstructs its remote command line. Do not place
        # multiline Python (or JSON) in shell arguments: quoting it with POSIX
        # shlex.quote does not protect it from Windows argument reconstruction.
        # Python reads this wrapper from stdin; the existing server_ops entry
        # point then reads the request from an in-memory stream, unchanged.
        program = ('import io, sys\n'
                   f'sys.stdin = io.StringIO({json.dumps(request)!r})\n'
                   f'exec(compile({source!r}, "server_ops.py", "exec"), '
                   '{"__name__": "__main__"})\n')
        command = 'sudo -n python3 -'
        result = subprocess.run(['ssh', *self.options, HOST, command],
            input=program, capture_output=True, text=True, encoding='utf-8', check=True)
        return json.loads(result.stdout)

    def upload(self, source):
        target = '/tmp/mf-release-' + uuid.uuid4().hex
        # -O uses legacy SCP and a POSIX remote basename; no Windows path goes to the remote shell.
        subprocess.run(['scp', '-O', *self.options, str(Path(source).resolve()), HOST + ':' + target], check=True)
        return target

    def download(self, relative, target):
        if not relative.startswith('releases/bootstrap/') or '..' in Path(relative).parts:
            raise ValueError('refusing unexpected bootstrap download path')
        target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['scp', '-O', *self.options, HOST + ':/opt/mentorflow/update-static/e108/app/' + relative,
                        str(target.resolve())], check=True)
        return target


def fetched(server, version, path, *, staged=False, live=False):
    response = server.call('live' if live else 'fetch', version, staged=staged)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(base64.b64decode(response['raw']))
    return Path(path)
