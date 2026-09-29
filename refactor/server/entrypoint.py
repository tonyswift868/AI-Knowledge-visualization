"""Existing RP-05 secret-to-environment adapter, limited to the backend process."""
import os
from pathlib import Path
import sys
from urllib.parse import quote


def runtime_environment(source=None, secret_root=Path('/run/secrets')):
    env = dict(os.environ if source is None else source)
    env['MENTORFLOW_DEPLOYMENT_MODE'] = 'ENTERPRISE_SERVER'
    # Existing deployments mount postgres_app_password instead of exporting a DSN.
    # An explicit DSN still works for standalone installations.
    if not env.get('MENTORFLOW_DATABASE_URL'):
        password = (secret_root / 'postgres_app_password').read_text(encoding='utf-8').strip()
        if not password:
            raise RuntimeError('required secret file is empty: postgres_app_password')
        env['MENTORFLOW_DATABASE_URL'] = (
            'postgresql+psycopg://mentorflow_app:' + quote(password, safe='') +
            '@postgres:5432/mentorflow?connect_timeout=2')
    env.pop('MENTORFLOW_MIGRATION_DATABASE_URL', None)
    return env


def main():
    env = runtime_environment()
    # Preserve RP-05's reverse proxy handling: native routes require the HTTPS scheme.
    argv = [sys.executable, '-m', 'uvicorn', 'refactor.server.app:app',
            '--host', '0.0.0.0', '--port', '8000', '--proxy-headers', '--forwarded-allow-ips=*']
    os.execvpe(argv[0], argv, env)


if __name__ == '__main__':
    main()
