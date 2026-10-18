"""Architect-only local signing. Only the signed manifest crosses SSH."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_learning_installer as builder
from scripts.release.pipeline import PUBLIC, stage_manifest, verified_release
from refactor.desktop.delivery.update_protocol import make_login_policy
from scripts.release.transport import LocalServer, SSHServer


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--artifact', type=Path, required=True, help='downloaded ZIP, directory or unsigned JSON')
    p.add_argument('--private-key', type=Path, required=True)
    p.add_argument('--public-key', type=Path, default=PUBLIC)
    p.add_argument('--work', type=Path, required=True)
    p.add_argument('--local-server-root', type=Path)
    p.add_argument('--ssh-identity', type=Path)
    args = p.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    unsigned = args.artifact
    if unsigned.is_dir():
        candidates = list(unsigned.rglob('unsigned-release.json'))
        if len(candidates) != 1:
            p.error('artifact must contain exactly one unsigned-release.json')
        unsigned = candidates[0]
    elif zipfile.is_zipfile(unsigned):
        with zipfile.ZipFile(unsigned) as archive:
            names = [n for n in archive.namelist() if n.split('/')[-1] == 'unsigned-release.json']
            if len(names) != 1:
                p.error('ZIP must contain exactly one unsigned-release.json')
            unsigned = args.work / 'unsigned-release.json'
            unsigned.write_bytes(archive.read(names[0]))
    stable = args.work / 'stable.json'
    # Existing signer validates private/public identity; no new signing implementation.
    builder.sign(argparse.Namespace(unsigned=unsigned, public_key=args.public_key,
                                    private_key=args.private_key, output=stable))
    server = LocalServer(args.local_server_root) if args.local_server_root else SSHServer(
        args.work / 'ssh', identity=args.ssh_identity)
    release = verified_release(stable, args.public_key)
    staged = stage_manifest(server, stable, args.public_key)
    expected_policy = make_login_policy(release)
    if staged.get('login_policy') != expected_policy:
        raise RuntimeError('server staged login policy does not match signed manifest')
    print(staged)


if __name__ == '__main__':
    main()
