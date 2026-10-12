"""Offline real-installer rehearsal, isolated from formal and other UAT installs."""
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
import base64
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
TASK = Path('D:/MentorFlow/.local/test-tmp/dir105')
APP_ID = 'MentorFlow.UAT.dir105'
# No server is started. Both account and update requests fail locally.
FEED = 'http://127.0.0.1:18805/updates/app/stable/stable.json'
sys.path[:0] = [str(ROOT), 'D:/MentorFlow/.local/test-tmp/installer/build-tools']
from scripts import build_learning_installer as b
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from refactor.desktop.delivery.gui_manifest import collect_closure
from refactor.desktop.delivery.package_verification import build_manifest


def main():
    for name in ('temp', 'logs', 'screenshots', 'custom'):
        (TASK / name).mkdir(parents=True, exist_ok=True)
    os.environ.update(TEMP=str(TASK/'temp'), TMP=str(TASK/'temp'),
        TMPDIR=str(TASK/'temp'), PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=os.pathsep.join(sys.path[:2]),
        MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps')
    private = Ed25519PrivateKey.generate()  # Only in memory; never a formal key.
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    (TASK/'public.json').write_bytes(b.canonical(dict(key_id='dir105-test',
        public_key=base64.b64encode(public).decode())))
    (TASK/'public-runtime.json').write_bytes(b.canonical({
        'MENTORFLOW_DESKTOP_OIDC_ISSUER':'https://localhost:18805/test-realm',
        'MENTORFLOW_DESKTOP_IDENTITY_ISSUER':'https://localhost:18805/test-realm',
        'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID':'mentorflow-desktop',
        'MENTORFLOW_DESKTOP_OIDC_AUDIENCE':'mentorflow-api',
        'MENTORFLOW_ENTERPRISE_API_BASE_URL':'https://127.0.0.1:18805'}))
    web = TASK/'web'
    b.run(['D:/MentorFlow/.local/node/node.exe',
        'D:/MentorFlow/.local/web-deps/node_modules/vite/bin/vite.js', 'build',
        '--config', ROOT/'refactor/learning/web/vite.config.mjs', '--outDir', web],
        TASK/'temp', TASK/'logs/web.log')
    inputs = Path('D:/MentorFlow/.local/release-tools/inputs-2.0.1')
    b.freeze(SimpleNamespace(work=TASK/'freeze', public_key=TASK/'public.json',
        feed=FEED, app_id=APP_ID, version='2.0.14',
        voice_catalog=inputs/'component-catalog.json', seven_zip=inputs/'7zr.exe',
        public_runtime=TASK/'public-runtime.json', account_ca=None, web_dist=web))
    payload = TASK/'freeze/app/MentorFlow'
    check = payload/'SELF-CHECK'
    check.mkdir(exist_ok=True)
    (check/'gui-bootstrap-closure.json').write_bytes(b.canonical(collect_closure(payload)))
    manifest_path = build_manifest(payload)
    manifest = json.loads(manifest_path.read_bytes())
    manifest_path.write_bytes(b.canonical(manifest))
    rows = manifest['files'] + [dict(path='SELF-CHECK/package-manifest.json',
        size=manifest_path.stat().st_size, sha256=b.digest(manifest_path))]
    assets = [{**r, 'url':FEED.removesuffix('stable/stable.json')+'objects/'+r['sha256']} for r in rows]
    now = datetime.now(timezone.utc)
    def git(ref):
        return subprocess.check_output(['git','rev-parse',ref], cwd=ROOT, text=True).strip()
    signed = dict(schema_version=1, app_id=APP_ID, channel='stable', version='2.0.14',
        metadata_version=105, issued_at=now.isoformat(), published_at=now.isoformat(),
        expires_at=(now+timedelta(days=3)).isoformat(), key_id='dir105-test',
        rollout_percentage=100, rollout_seed='dir105', withdrawn=False,
        min_supported_version='0.0.0', min_updater_version='1.0.0',
        mandatory=False, mandatory_reason='', release_notes='Offline directory UAT',
        full_release=dict(format='file-set-v1',size=sum(r['size'] for r in rows),assets=assets),
        incremental_assets=[r['path'] for r in rows], file_manifest=manifest,
        hash_algorithm='sha256', signature_algorithm='ed25519', base_versions=[],
        source_sha=git('HEAD'), tree_sha=git('HEAD^{tree}'))
    release = TASK/'stable.json'
    release.write_bytes(b.canonical(dict(signed=signed, key_id='dir105-test',
        signature=base64.b64encode(private.sign(b.canonical(signed))).decode())))
    original = b.run
    def own_shortcuts(command, work, log):
        # Other parallel UATs use the generic preview shortcut. Keep ours unique.
        command = ['/DShortcutName=MentorFlow dir105 预览版' if str(c).startswith('/DShortcutName=') else c for c in command]
        original(command, work, log)
    b.run = own_shortcuts
    try:
        b.installer(SimpleNamespace(payload=payload, release=release,
            work=TASK/'inno', output=TASK/'setup',
            compiler=Path('E:/Codex/App/InnoSetup-7.1.0/ISCC.exe')))
    finally:
        b.run = original


if __name__ == '__main__':
    main()
