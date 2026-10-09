"""Produce the small server tool bundle, including PUBLIC trust from a trusted exe."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
import tarfile
import io

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_bundle(payload, output):
    from PyInstaller.archive.readers import CArchiveReader
    from refactor.desktop.packaging.authority import literal_authority
    authorities = [literal_authority(CArchiveReader(str(payload / name)).open_embedded_archive("PYZ.pyz")
                    .extract("mentorflow_delivery_authority"))
                   for name in ("MentorFlow.exe", "UserRuntime/UserRuntime.exe")]
    if authorities[0] != authorities[1] or authorities[0]["APP_ID"] != "MentorFlow":
        raise ValueError("trusted MentorFlow client/updater authority mismatch")
    if len(authorities[0]["PUBLIC_KEYS"]) != 1:
        raise ValueError("select the active public release key before making this bundle")
    key_id, public = next(iter(authorities[0]["PUBLIC_KEYS"].items()))
    trust = json.dumps(dict(key_id=key_id, public_key=base64.b64encode(public).decode()), indent=2).encode() + b"\n"
    files = [ROOT / "scripts/build_learning_installer.py"]
    files += sorted((ROOT / "scripts/server_installer").glob("*"))
    files += sorted((ROOT / "refactor/desktop/packaging/inno").glob("*.iss"))
    files += [ROOT / "refactor/desktop/packaging" / name for name in ("authority.py", "payload.py")]
    files += [ROOT / "refactor/desktop/delivery" / name for name in (
        "update_protocol.py", "per_user_install.py", "install_product.py", "install_location.py", "package_verification.py")]
    files += [ROOT / "refactor/desktop/resources/teacher2d/tsubaki-live2d/icon.png"]
    # Namespace __init__ files may exist; preserve them without pulling unrelated modules.
    files += [p / "__init__.py" for p in (ROOT / "scripts", ROOT / "refactor", ROOT / "refactor/desktop",
              ROOT / "refactor/desktop/delivery", ROOT / "refactor/desktop/packaging") if (p / "__init__.py").is_file()]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for path in files:
            if path.is_file() and path.name != "trust.json":
                name = path.relative_to(ROOT).as_posix()
                if path.suffix == ".sh" or path.name == "Dockerfile":
                    # A Windows checkout may use CRLF; Bash/Docker need LF.
                    data = path.read_bytes().replace(b"\r\n", b"\n")
                    info = archive.gettarinfo(str(path), arcname=name)
                    info.size = len(data)
                    if path.suffix == ".sh":
                        info.mode = 0o755
                    archive.addfile(info, io.BytesIO(data))
                else:
                    archive.add(path, arcname=name)
        info = tarfile.TarInfo("scripts/server_installer/trust.json")
        info.size = len(trust)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(trust))
    return dict(bundle=str(output), size=output.stat().st_size, key_id=key_id,
                files=len([p for p in files if p.is_file()]) + 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True, help="already trusted local release payload")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(make_bundle(args.payload, args.output)))
