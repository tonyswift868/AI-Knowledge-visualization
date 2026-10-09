"""Container-side coordinator. No download, signing, installer execution or App data."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_learning_installer as builder
from scripts.server_installer.assemble_payload import assemble_payload, verified_release


def current_installer(directory, version):
    """An incomplete previous publish is not mistaken for a completed version."""
    try:
        record = json.loads((directory / "VERSION.txt").read_text(encoding="utf-8"))
        exe = directory / "MentorFlow-Setup.exe"
        return (record["version"] == version and exe.stat().st_size == record["size"]
                and builder.digest(exe) == record["sha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def publish_installer(candidate, directory, state, version):
    """Backup before atomic exe replacement; rollback on metadata write failure."""
    directory.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    exe, metadata = directory / "MentorFlow-Setup.exe", directory / "VERSION.txt"
    stage, stage_metadata = directory / ".MentorFlow-Setup.exe.new", directory / ".VERSION.txt.new"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    prior_metadata = metadata.read_bytes() if metadata.exists() else None
    backup = None
    replaced = False
    record = dict(version=version, sha256=builder.digest(candidate), size=candidate.stat().st_size,
                  built_at=datetime.now(timezone.utc).isoformat())
    try:
        shutil.copyfile(candidate, stage)
        stage.chmod(0o644)
        if builder.digest(stage) != record["sha256"] or stage.stat().st_size != record["size"]:
            raise ValueError("staged installer hash/size mismatch")
        with stage_metadata.open("wb") as stream:
            stream.write(builder.canonical(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        stage_metadata.chmod(0o644)
        if exe.exists():
            # Date is always safe, also when the previous VERSION is absent/old text.
            backup = state / f"installer-{stamp}.exe"
            shutil.copy2(exe, backup)
            if builder.digest(backup) != builder.digest(exe):
                raise ValueError("previous installer backup mismatch")
        os.replace(stage, exe)
        replaced = True
        os.replace(stage_metadata, metadata)
    except BaseException:
        if replaced:
            if backup is not None:
                shutil.copy2(backup, stage)
                os.replace(stage, exe)
            else:
                exe.unlink()
            # Metadata replace is atomic: failed replace leaves its prior bytes.
            if prior_metadata is None and metadata.exists():
                metadata.unlink()
        raise
    finally:
        stage.unlink(missing_ok=True)
        stage_metadata.unlink(missing_ok=True)
    return {**record, "backup": str(backup) if backup else None}


def build_on_server(app_root, state_root, public_key, compiler, *, wine=None, compile_fn=None):
    app_root, state_root = Path(app_root), Path(state_root)
    stable = app_root / "stable/stable.json"
    # Verify before creating any per-release build output or inspecting version policy.
    release = verified_release(stable, public_key)
    original = release.envelope
    if current_installer(app_root / "installer", release.version):
        return dict(status="UP_TO_DATE", version=release.version)
    base = state_root / "installer-build" / release.version
    base.mkdir(parents=True, exist_ok=True)
    # A unique attempt preserves failure logs while allowing a clean retry.
    work = Path(tempfile.mkdtemp(prefix="attempt-", dir=base))
    snapshot = work / "stable.json"
    snapshot.write_bytes(original)
    log = work / "build-status.json"
    try:
        stats = assemble_payload(snapshot, app_root / "objects", work / "payload", public_key=public_key)
        print("ASSEMBLED " + json.dumps(stats), flush=True)
        args = argparse.Namespace(payload=work / "payload", release=snapshot, work=work / "inno",
                                  output=work / "output", compiler=Path(compiler), wine=wine)
        (compile_fn or builder.installer)(args)
        candidate = args.output / "MentorFlow-Setup.exe"
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise ValueError("compiler produced no installer")
        if stable.read_bytes() != original:
            raise ValueError("stable changed during build; retry against the new release")
        result = publish_installer(candidate, app_root / "installer", state_root, release.version)
        result.update(status="PUBLISHED", payload=stats)
    except BaseException as error:
        log.write_text(json.dumps(dict(status="FAILED", error=str(error)), ensure_ascii=False), encoding="utf-8")
        # No bulky payload/compiler scratch retained on failure; preserve its log.
        if (work / "inno/inno.log").is_file():
            shutil.copyfile(work / "inno/inno.log", work / "inno.log")
        for name in ("payload", "output", "inno"):
            if (work / name).exists():
                shutil.rmtree(work / name)
        print(f"FAILED; evidence: {work}", file=sys.stderr, flush=True)
        raise
    try:
        shutil.rmtree(work)
        if not any(base.iterdir()):
            base.rmdir()
    except OSError as error:
        # Publish has committed; a cleanup problem must not be reported as rollback.
        result["cleanup_warning"] = f"{work}: {error}"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, default=Path("/srv/app"))
    parser.add_argument("--state-root", type=Path, default=Path("/srv/state"))
    parser.add_argument("--public-key", type=Path, default=Path(__file__).with_name("trust.json"))
    parser.add_argument("--compiler", type=Path, default=Path("/opt/wine/drive_c/InnoSetup/ISCC.exe"))
    parser.add_argument("--wine", type=Path, default=Path("/usr/bin/wine"))
    args = parser.parse_args()
    print(json.dumps(build_on_server(args.app_root, args.state_root, args.public_key,
                                    args.compiler, wine=args.wine), ensure_ascii=False))


if __name__ == "__main__":
    main()
