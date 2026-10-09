"""Reconstruct a signed file-set using the existing release and tree verifiers."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_learning_installer import digest, read_key
from refactor.desktop.delivery.update_protocol import verify_release, SecurityError
from refactor.desktop.delivery.per_user_install import verify_tree


def verified_release(path, public_key):
    key_id, public = read_key(public_key)
    return verify_release(Path(path).read_bytes(), {key_id: public}, current="0.0.0")


def assemble_payload(release_path, objects, output, *, public_key):
    """Publish output only after every object and the complete tree pass verification.

    public_key is a publisher-provisioned PUBLIC key JSON, extracted from a trusted
    package when preparing the tool bundle, never from the unverified manifest.
    Existing output is preserved. Objects are read-only inputs even when linked.
    """
    release = verified_release(release_path, public_key)
    objects, output = Path(objects).resolve(), Path(output).absolute()
    if output.exists():
        raise FileExistsError(f"existing output preserved: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    linked = copied = 0
    try:
        for name, row in release.rows.items():
            source = objects / row["sha256"]
            if not source.is_file():
                raise FileNotFoundError(f"missing object for {name}: {source.name}")
            if source.stat().st_size != row["size"] or digest(source) != row["sha256"]:
                raise SecurityError(f"object size/hash mismatch: {name} ({source.name})")
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(source, target)
                linked += 1
            except OSError:
                shutil.copyfile(source, target)
                copied += 1
        verify_tree(staged, release)
        staged.rename(output)
    except BaseException:
        shutil.rmtree(staged)
        raise
    return dict(version=release.version, files=len(release.rows),
                bytes=sum(row["size"] for row in release.rows.values()),
                linked=linked, copied=copied, output=str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release", "objects", "output", "public-key"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(assemble_payload(args.release, args.objects, args.output,
                                     public_key=args.public_key), ensure_ascii=False))


if __name__ == "__main__":
    main()
